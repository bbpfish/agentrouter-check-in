#!/usr/bin/env python3
"""
多站点自动签到脚本 - AgentRouter + HCN
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from notify import notify

load_dotenv()

BALANCE_HASH_FILE = 'balance_hash.txt'

# 站点配置
SITES = {
    'hcnsec': {
        'name': 'HCN',
        'base_url': 'https://api.hcnsec.cn',
        'sign_in_path': '/api/user/checkin',
        'user_info_path': '/api/user/self',
        'accounts_env': 'HCN_ACCOUNTS',
        'needs_waf': False,
        # HCN 签到成功判断：success==true
        'check_success': lambda r: r.get('success') == True,
        # HCN 余额直接显示（不换算）
        'quota_divisor': 1,
    },
}


def load_accounts(env_var):
    """从环境变量加载多账号配置"""
    accounts_str = os.getenv(env_var)
    if not accounts_str:
        return None

    try:
        accounts_data = json.loads(accounts_str)
        if not isinstance(accounts_data, list):
            print(f'ERROR: {env_var} must use array format [{{}}]')
            return None

        for i, account in enumerate(accounts_data):
            if not isinstance(account, dict):
                print(f'ERROR: Account {i + 1} configuration format is incorrect')
                return None
            if 'cookies' not in account or 'api_user' not in account:
                print(f'ERROR: Account {i + 1} missing required fields (cookies, api_user)')
                return None
            if 'name' in account and not account['name']:
                print(f'ERROR: Account {i + 1} name field cannot be empty')
                return None

        return accounts_data
    except Exception as e:
        print(f'ERROR: Account configuration format is incorrect: {e}')
        return None


def load_balance_hash():
    """加载余额hash"""
    try:
        if os.path.exists(BALANCE_HASH_FILE):
            with open(BALANCE_HASH_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
    except Exception:
        pass
    return None


def save_balance_hash(balance_hash):
    """保存余额hash"""
    try:
        with open(BALANCE_HASH_FILE, 'w', encoding='utf-8') as f:
            f.write(balance_hash)
    except Exception as e:
        print(f'Warning: Failed to save balance hash: {e}')


def generate_balance_hash(balances):
    """生成余额数据的hash"""
    simple_balances = {k: v['quota'] for k, v in balances.items()} if balances else {}
    balance_json = json.dumps(simple_balances, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(balance_json.encode('utf-8')).hexdigest()[:16]


def get_account_display_name(account_info, account_index):
    """获取账号显示名称"""
    return account_info.get('name', f'Account {account_index + 1}')


def parse_cookies(cookies_data):
    """解析 cookies 数据，支持 dict / string / list 格式"""
    if isinstance(cookies_data, dict):
        return cookies_data
    if isinstance(cookies_data, list):
        cookies_dict = {}
        for item in cookies_data:
            if isinstance(item, dict) and 'name' in item and 'value' in item:
                cookies_dict[item['name']] = item['value']
        return cookies_dict
    if isinstance(cookies_data, str):
        cookies_dict = {}
        for cookie in cookies_data.split(';'):
            if '=' in cookie:
                key, value = cookie.strip().split('=', 1)
                cookies_dict[key] = value
        return cookies_dict
    return {}


async def get_waf_cookies_with_playwright(site_config, account_name):
    """使用 Playwright 获取 WAF cookies（隐私模式）"""
    site_name = site_config['name']
    base_url = site_config['base_url']
    print(f'[PROCESSING] {account_name}: Starting browser to get WAF cookies for {site_name}...')

    async with async_playwright() as p:
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=temp_dir,
                headless=True,
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--no-sandbox',
                ],
            )

            page = await context.new_page()

            try:
                print(f'[PROCESSING] {account_name}: Accessing {base_url} to get WAF cookies...')
                await page.goto(f'{base_url}/login', wait_until='networkidle')

                try:
                    await page.wait_for_function('document.readyState === "complete"', timeout=5000)
                except Exception:
                    await page.wait_for_timeout(3000)

                cookies = await page.context.cookies()
                waf_cookies = {}
                for cookie in cookies:
                    cookie_name = cookie.get('name')
                    cookie_value = cookie.get('value')
                    if cookie_name in ['acw_tc', 'cdn_sec_tc', 'acw_sc__v2'] and cookie_value is not None:
                        waf_cookies[cookie_name] = cookie_value

                print(f'[INFO] {account_name}: Got {len(waf_cookies)} WAF cookies')
                if waf_cookies:
                    print(f'[SUCCESS] {account_name}: WAF cookies: {list(waf_cookies.keys())}')
                else:
                    print(f'[WARN] {account_name}: No WAF cookies found, proceeding anyway')

                await context.close()
                return waf_cookies

            except Exception as e:
                print(f'[WARN] {account_name}: Failed to get WAF cookies: {e}')
                await context.close()
                return {}


def get_user_info(client, headers, site_config):
    """获取用户信息"""
    base_url = site_config['base_url']
    user_info_path = site_config['user_info_path']
    divisor = site_config['quota_divisor']

    try:
        response = client.get(f'{base_url}{user_info_path}', headers=headers, timeout=30)

        if response.status_code == 200:
            if not response.text or not response.text.strip():
                return {'success': False, 'error': 'Empty response from API'}
            try:
                data = response.json()
            except json.JSONDecodeError:
                return {'success': False, 'error': f'Invalid JSON response: {response.text[:100]}'}
            if data.get('success') or 'data' in data:
                user_data = data.get('data', {})
                raw_quota = user_data.get('quota', 0)
                raw_used = user_data.get('used_quota', 0)

                if divisor != 1:
                    quota = round(raw_quota / divisor, 2)
                    used_quota = round(raw_used / divisor, 2)
                else:
                    quota = raw_quota
                    used_quota = raw_used

                return {
                    'success': True,
                    'quota': quota,
                    'used_quota': used_quota,
                    'display': f':money: Current balance: ${quota}, Used: ${used_quota}'
                }
            return {'success': False, 'error': f'API returned: {data.get("message", "Unknown error")}'}
        return {'success': False, 'error': f'HTTP {response.status_code}: {response.text[:100] if response.text else "No content"}'}
    except Exception as e:
        return {'success': False, 'error': f'Failed to get user info: {str(e)[:100]}'}


async def check_in_account(site_config, account_info, account_index):
    """为单个站点单个账号执行签到操作"""
    site_name = site_config['name']
    base_url = site_config['base_url']
    sign_in_path = site_config['sign_in_path']
    needs_waf = site_config.get('needs_waf', False)
    check_success = site_config['check_success']

    account_name = get_account_display_name(account_info, account_index)
    print(f'\n[PROCESSING] [{site_name}] Starting to process {account_name}')

    cookies_data = account_info.get('cookies', {})
    api_user = account_info.get('api_user', '')

    if not api_user:
        print(f'[FAILED] [{site_name}] {account_name}: API user identifier not found')
        return False, None

    user_cookies = parse_cookies(cookies_data)
    if not user_cookies:
        print(f'[FAILED] [{site_name}] {account_name}: Invalid configuration format')
        return False, None

    # 获取 WAF cookies（如果站点需要）
    waf_cookies = {}
    if needs_waf:
        waf_cookies = await get_waf_cookies_with_playwright(site_config, account_name)
        if not waf_cookies:
            print(f'[WARN] [{site_name}] {account_name}: No WAF cookies obtained, continuing without them')

    client = httpx.Client(timeout=30.0)

    try:
        all_cookies = {**waf_cookies, **user_cookies}
        client.cookies.update(all_cookies)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Referer': f'{base_url}/console',
            'Origin': base_url,
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'new-api-user': api_user,
        }

        user_info = get_user_info(client, headers, site_config)
        if user_info and user_info.get('success'):
            print(user_info['display'])
        elif user_info:
            print(user_info.get('error', 'Unknown error'))

        print(f'[NETWORK] [{site_name}] {account_name}: Executing check-in')

        checkin_headers = headers.copy()
        checkin_headers.update({'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

        response = client.post(f'{base_url}{sign_in_path}', headers=checkin_headers, timeout=30)
        print(f'[RESPONSE] [{site_name}] {account_name}: Response status code {response.status_code}')

        if response.status_code == 200:
            try:
                result = response.json()
                if check_success(result):
                    print(f'[SUCCESS] [{site_name}] {account_name}: Check-in successful!')
                    return True, user_info
                else:
                    error_msg = result.get('msg', result.get('message', 'Unknown error'))
                    print(f'[INFO] [{site_name}] {account_name}: Check-in result - {error_msg}')
                    # "今日已签到"也算成功
                    if '已签到' in str(error_msg) or 'already' in str(error_msg).lower():
                        print(f'[SUCCESS] [{site_name}] {account_name}: Already checked in today')
                        return True, user_info
                    return False, user_info
            except json.JSONDecodeError:
                if 'success' in response.text.lower():
                    print(f'[SUCCESS] [{site_name}] {account_name}: Check-in successful!')
                    return True, user_info
                else:
                    print(f'[FAILED] [{site_name}] {account_name}: Check-in failed - Invalid response format')
                    return False, user_info
        else:
            print(f'[FAILED] [{site_name}] {account_name}: Check-in failed - HTTP {response.status_code}')
            return False, user_info

    except Exception as e:
        print(f'[FAILED] [{site_name}] {account_name}: Error occurred during check-in process - {str(e)[:50]}...')
        return False, None
    finally:
        client.close()


async def process_site(site_key, site_config):
    """处理单个站点的所有账号签到"""
    site_name = site_config['name']
    env_var = site_config['accounts_env']

    print(f'\n{"=" * 60}')
    print(f'[SYSTEM] {site_name} auto check-in started')
    print(f'{"=" * 60}')

    accounts = load_accounts(env_var)
    if accounts is None:
        print(f'[INFO] {site_name}: No account configuration found ({env_var} not set), skipping')
        return []

    print(f'[INFO] {site_name}: Found {len(accounts)} account(s)')

    results = []
    for i, account in enumerate(accounts):
        try:
            success, user_info = await check_in_account(site_config, account, i)
            account_name = get_account_display_name(account, i)
            results.append({
                'site': site_name,
                'site_key': site_key,
                'account_name': account_name,
                'success': success,
                'user_info': user_info,
            })
        except Exception as e:
            account_name = get_account_display_name(account, i)
            print(f'[FAILED] [{site_name}] {account_name} processing exception: {e}')
            results.append({
                'site': site_name,
                'site_key': site_key,
                'account_name': account_name,
                'success': False,
                'user_info': None,
            })

    return results


async def main():
    """主函数"""
    print('[SYSTEM] Multi-site auto check-in script started')
    print(f'[TIME] Execution time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

    # 加载余额hash
    last_balance_hash = load_balance_hash()

    # 处理所有站点
    all_results = []
    for site_key, site_config in SITES.items():
        results = await process_site(site_key, site_config)
        all_results.extend(results)

    if not all_results:
        print('[FAILED] No accounts configured for any site, program exits')
        sys.exit(1)

    # 汇总统计
    total_count = len(all_results)
    success_count = sum(1 for r in all_results if r['success'])
    failed_count = total_count - success_count

    # 构建通知内容
    current_balances = {}
    notification_content = []
    need_notify = False
    balance_changed = False

    for r in all_results:
        account_key = f"{r['site']}_{r['account_name']}"
        user_info = r['user_info']

        if not r['success']:
            need_notify = True
            status_line = f'[FAIL] [{r["site"]}] {r["account_name"]}'
            if user_info:
                status_line += f'\n{user_info.get("error", "Unknown error")}'
            notification_content.append(status_line)

        if user_info and user_info.get('success'):
            current_balances[account_key] = {'quota': user_info['quota'], 'used': user_info['used_quota']}

    # 检查余额变化
    current_balance_hash = generate_balance_hash(current_balances) if current_balances else None
    if current_balance_hash:
        if last_balance_hash is None:
            balance_changed = True
            need_notify = True
            print('[NOTIFY] First run detected, will send notification with current balances')
        elif current_balance_hash != last_balance_hash:
            balance_changed = True
            need_notify = True
            print('[NOTIFY] Balance changes detected, will send notification')
        else:
            print('[INFO] No balance changes detected')

    # 余额变化时追加余额信息
    if balance_changed:
        for r in all_results:
            account_key = f"{r['site']}_{r['account_name']}"
            if account_key in current_balances:
                bal = current_balances[account_key]
                bal_line = f'[BALANCE] [{r["site"]}] {r["account_name"]}\n:money: Current balance: ${bal["quota"]}, Used: ${bal["used"]}'
                if not any(f'[{r["site"]}] {r["account_name"]}' in item for item in notification_content):
                    notification_content.append(bal_line)

    # 保存余额hash
    if current_balance_hash:
        save_balance_hash(current_balance_hash)

    # 按站点分组统计
    site_stats = {}
    for r in all_results:
        sk = r['site']
        if sk not in site_stats:
            site_stats[sk] = {'total': 0, 'success': 0}
        site_stats[sk]['total'] += 1
        if r['success']:
            site_stats[sk]['success'] += 1

    summary = [
        '[STATS] Check-in result statistics:',
    ]
    for site_name, stats in site_stats.items():
        summary.append(f'[{site_name}] {stats["success"]}/{stats["total"]} success')
    summary.append(f'[TOTAL] {success_count}/{total_count} success')

    if success_count == total_count:
        summary.append('[SUCCESS] All accounts check-in successful!')
    elif success_count > 0:
        summary.append('[WARN] Some accounts check-in successful')
    else:
        summary.append('[ERROR] All accounts check-in failed')

    time_info = f'[TIME] Execution time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'

    if need_notify and notification_content:
        notify_content = '\n\n'.join([time_info, '\n'.join(notification_content), '\n'.join(summary)])
        print(notify_content)
        notify.push_message('Multi-Site Check-in Alert', notify_content, msg_type='text')
        print('[NOTIFY] Notification sent')
    else:
        print('[INFO] All accounts successful and no balance changes detected, notification skipped')

    sys.exit(0 if success_count > 0 else 1)


def run_main():
    """运行主函数的包装函数"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n[WARNING] Program interrupted by user')
        sys.exit(1)
    except Exception as e:
        print(f'\n[FAILED] Error occurred during program execution: {e}')
        sys.exit(1)


if __name__ == '__main__':
    run_main()
