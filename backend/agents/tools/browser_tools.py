"""
Browser Tools — Playwright browser automation executed in Docker sandbox.
Uses a persistent browser instance via CDP (Chrome DevTools Protocol).
"""
import asyncio
import base64
import json
import time
from docker_manager.manager import docker_manager


# Script that runs persistently in the sandbox to keep browser alive
BROWSER_SERVER_SCRIPT = '''#!/usr/bin/env python3
"""Persistent browser server using Playwright. Keeps browser alive between actions."""
import asyncio
import json
import sys
import os
import signal

from playwright.async_api import async_playwright

SOCKET_PATH = "/tmp/browser_server.sock"
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
"""

browser = None
context = None
page = None


async def ensure_browser():
    """Ensure browser is running, start if not."""
    global browser, context, page
    
    if browser and browser.is_connected():
        if page and not page.is_closed():
            return page
        # Page closed, create new one
        page = await context.new_page()
        return page
    
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
        ]
    )
    context = await browser.new_context(
        viewport={'width': 1280, 'height': 720},
        ignore_https_errors=True,
        user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        locale='en-US',
        timezone_id='America/New_York',
    )
    await context.add_init_script(STEALTH_SCRIPT)
    page = await context.new_page()
    return page


async def handle_action(action, args):
    """Handle a browser action."""
    import base64
    from datetime import datetime
    
    pg = await ensure_browser()
    
    if action == 'navigate':
        url = args.get('url', '')
        await pg.goto(url, wait_until='domcontentloaded', timeout=30000)
        # Wait a bit more for dynamic content
        await asyncio.sleep(1)
        return {'success': True, 'url': pg.url, 'title': await pg.title()}
    
    elif action == 'click':
        selector = args.get('selector', '')
        # Try CSS selector first, then text
        try:
            await pg.click(selector, timeout=5000)
        except Exception:
            # Try by text content
            await pg.click(f'text="{selector}"', timeout=5000)
        await asyncio.sleep(0.5)
        return {'success': True, 'clicked': selector, 'url': pg.url}
    
    elif action == 'type':
        selector = args.get('selector', '')
        text = args.get('text', '')
        await pg.fill(selector, text)
        return {'success': True, 'typed': text, 'selector': selector}
    
    elif action == 'screenshot':
        full_page = args.get('full_page', False)
        screenshot = await pg.screenshot(full_page=full_page, type='png')
        screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
        # Save to file
        screenshots_dir = '/workspace/.screenshots'
        os.makedirs(screenshots_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = f'{screenshots_dir}/screenshot_{timestamp}.png'
        with open(path, 'wb') as f:
            f.write(screenshot)
        return {
            'success': True,
            'screenshot': screenshot_b64[:100] + '...(truncated)',
            'screenshot_path': path.replace('/workspace/', ''),
            'full_base64_length': len(screenshot_b64),
        }
    
    elif action == 'get_content':
        selector = args.get('selector', '')
        if selector:
            text = await pg.text_content(selector)
        else:
            text = await pg.evaluate('document.body.innerText')
        return {'success': True, 'content': (text or '')[:5000], 'url': pg.url}
    
    elif action == 'wait':
        selector = args.get('selector', '')
        timeout = args.get('timeout', 5000)
        await pg.wait_for_selector(selector, timeout=timeout)
        return {'success': True, 'found': selector}
    
    elif action == 'close':
        if browser:
            await browser.close()
        return {'success': True, 'closed': True}
    
    else:
        return {'success': False, 'error': f'Unknown action: {action}'}


async def main():
    action = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    
    try:
        result = await handle_action(action, args)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e)}))


if __name__ == '__main__':
    asyncio.run(main())
'''

# Скрипт, который только запускает Chromium с CDP и не завершается — чтобы окно было видно в noVNC
BROWSER_KEEPALIVE_SCRIPT = '''#!/usr/bin/env python3
"""Keep Chromium running with CDP so noVNC shows the browser window."""
import asyncio
import os
import sys
os.environ.setdefault("DISPLAY", ":99")
from playwright.async_api import async_playwright

async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--remote-debugging-port=9222",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    )
    await context.new_page()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
'''

# Скрипт проверки: есть ли уже браузер на CDP (не закрываем его — только проверяем подключение)
BROWSER_PING_SCRIPT = '''#!/usr/bin/env python3
import asyncio
from playwright.async_api import async_playwright
async def main():
    try:
        p = await async_playwright().start()
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=2000)
        # не вызываем b.close() — иначе закроем браузер; просто выходим, соединение оборвётся
        print("OK")
    except Exception:
        print("FAIL")
if __name__ == "__main__":
    asyncio.run(main())
'''

# Simpler action script that connects to existing browser via CDP
BROWSER_ACTION_SCRIPT = '''#!/usr/bin/env python3
"""Execute a browser action, connecting to existing browser or starting new one."""
import asyncio
import json
import sys
import os
import base64
from datetime import datetime

async def main():
    action = sys.argv[1]
    if len(sys.argv) > 2 and os.path.isfile(sys.argv[2]):
        with open(sys.argv[2], 'r') as f:
            args = json.load(f)
    else:
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    
    from playwright.async_api import async_playwright
    
    CDP_URL = "http://127.0.0.1:9222"
    STEALTH_SCRIPT = "Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); window.chrome = { runtime: {} };"
    
    async with async_playwright() as p:
        browser = None
        page = None
        connected_to_existing = False

        async def attach_failure_artifacts(result_obj):
            """Attach debug metadata for failed actions so incidents are easier to investigate."""
            if not isinstance(result_obj, dict) or result_obj.get('success', True):
                return result_obj
            if not page:
                return result_obj

            try:
                screenshots_dir = '/workspace/.screenshots'
                os.makedirs(screenshots_dir, exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                debug_path = f'{screenshots_dir}/failure_{action}_{timestamp}.png'
                await page.screenshot(path=debug_path, full_page=True)
                result_obj['debug_screenshot_path'] = debug_path.replace('/workspace/', '')
            except Exception:
                pass

            try:
                result_obj['url'] = page.url
                result_obj['title'] = await page.title()
            except Exception:
                pass
            return result_obj
        
        # Try to connect to existing browser
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL, timeout=3000)
            contexts = browser.contexts
            if contexts:
                pages = contexts[0].pages
                if pages:
                    page = pages[0]
                    connected_to_existing = True
        except Exception:
            pass
        
        # If no existing browser, launch new one with CDP
        if not browser or not browser.is_connected():
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--remote-debugging-port=9222',
                ]
            )
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 720},
                ignore_https_errors=True,
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            )
            await context.add_init_script(STEALTH_SCRIPT)
            page = await context.new_page()
        
        if not page:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
        
        try:
            if action == 'navigate':
                url = args.get('url', '')
                timeout = int(args.get('timeout', 30000))
                await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
                await asyncio.sleep(0.6)
                result = {'success': True, 'url': page.url, 'title': await page.title()}
            
            elif action == 'click':
                selector = args.get('selector', '')
                timeout = int(args.get('timeout', 5000))
                last_err = None
                done = False
                for attempt in range(2):
                    try:
                        if attempt == 1:
                            await page.wait_for_selector(selector, state='visible', timeout=min(timeout, 2000))
                        await page.click(selector, timeout=timeout)
                        done = True
                        break
                    except Exception as e1:
                        last_err = e1
                        try:
                            await page.click('text="' + selector.replace('"', '\\\\"') + '"', timeout=timeout)
                            done = True
                            break
                        except Exception as e2:
                            last_err = e2
                            try:
                                await page.get_by_text(selector, exact=False).first.click(timeout=timeout)
                                done = True
                                break
                            except Exception as e3:
                                last_err = e3
                    if not done and attempt == 0:
                        await asyncio.sleep(0.8)
                if done:
                    await asyncio.sleep(0.25)
                    result = {'success': True, 'clicked': selector, 'url': page.url}
                else:
                    result = {'success': False, 'error': 'Element not found: %s. %s' % (selector, last_err)}
            
            elif action == 'type':
                selector = args.get('selector', '')
                text = args.get('text', '')
                timeout = int(args.get('timeout', 5000))
                last_err = None
                done = False
                for attempt in range(2):
                    try:
                        await page.fill(selector, text, timeout=timeout)
                        done = True
                        break
                    except Exception as e:
                        last_err = e
                    if not done and attempt == 0:
                        await asyncio.sleep(0.8)
                if done:
                    result = {'success': True, 'typed': text, 'selector': selector}
                else:
                    result = {'success': False, 'error': 'Input not found: %s. %s' % (selector, last_err)}
            
            elif action == 'fill_form':
                steps = args.get('steps', [])
                submit_selector = args.get('submit_selector', '')
                errors = []
                applied = []
                for i, step in enumerate(steps):
                    sel = step.get('selector', '')
                    val = step.get('value', '')
                    if not sel:
                        continue
                    try:
                        field_meta = await page.evaluate("""(selector) => {
                            const el = document.querySelector(selector);
                            if (!el) return { exists: false };
                            const tag = (el.tagName || '').toLowerCase();
                            const role = ((el.getAttribute('role') || '') + '').toLowerCase();
                            return {
                                exists: true,
                                tag,
                                role,
                                inputType: ((el.type || '') + '').toLowerCase(),
                                isSelect: tag === 'select',
                                isNativeMultiSelect: tag === 'select' && !!el.multiple,
                                isAriaMultiSelect: ['listbox', 'combobox'].includes(role) &&
                                    ((el.getAttribute('aria-multiselectable') || '') + '').toLowerCase() === 'true',
                            };
                        }""", sel)

                        if not field_meta or not field_meta.get('exists'):
                            raise Exception('element not found')

                        requested_type = (step.get('type') or '').lower()
                        wants_select = requested_type in ('select', 'dropdown', 'multiselect')
                        is_selectish = field_meta.get('isSelect') or field_meta.get('role') in ('combobox', 'listbox')
                        is_multi = field_meta.get('isNativeMultiSelect') or field_meta.get('isAriaMultiSelect') or requested_type == 'multiselect'

                        if wants_select or is_selectish:
                            selected = False
                            if step.get('values') and isinstance(step.get('values'), list):
                                await page.select_option(sel, [str(v) for v in step.get('values')])
                                selected = True
                            elif step.get('value'):
                                if is_multi and isinstance(step.get('value'), str) and ',' in step.get('value'):
                                    values = [x.strip() for x in step.get('value').split(',') if x.strip()]
                                    if values:
                                        await page.select_option(sel, values)
                                        selected = True
                                if not selected:
                                    await page.select_option(sel, value=str(step['value']))
                                    selected = True
                            elif step.get('label'):
                                await page.select_option(sel, label=str(step['label']))
                                selected = True

                            if not selected:
                                raise Exception('select field requires value/values/label')
                            applied.append({'selector': sel, 'mode': 'multiselect' if is_multi else 'select'})
                        else:
                            await page.fill(sel, str(val))
                            applied.append({'selector': sel, 'mode': 'text'})
                    except Exception as e:
                        errors.append('%s: %s' % (sel, e))
                if submit_selector:
                    try:
                        await page.click(submit_selector, timeout=5000)
                        await asyncio.sleep(0.4)
                    except Exception as e:
                        errors.append('submit %s: %s' % (submit_selector, e))
                if errors:
                    result = {'success': False, 'error': '; '.join(errors), 'url': page.url, 'applied': applied}
                else:
                    result = {'success': True, 'filled': len(steps), 'url': page.url, 'applied': applied}
            
            elif action == 'select':
                selector = args.get('selector', '')
                value = args.get('value', '')
                label = args.get('label', '')
                try:
                    if value:
                        await page.select_option(selector, value=value)
                        result = {'success': True, 'selector': selector, 'url': page.url}
                    elif label:
                        await page.select_option(selector, label=label)
                        result = {'success': True, 'selector': selector, 'url': page.url}
                    else:
                        result = {'success': False, 'error': 'Provide value or label for select'}
                except Exception as e:
                    result = {'success': False, 'error': 'Select failed: %s. %s' % (selector, e)}
            
            elif action == 'screenshot':
                full_page = args.get('full_page', False)
                screenshot = await page.screenshot(full_page=full_page, type='png')
                screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
                screenshots_dir = '/workspace/.screenshots'
                os.makedirs(screenshots_dir, exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                path = f'{screenshots_dir}/screenshot_{timestamp}.png'
                with open(path, 'wb') as f:
                    f.write(screenshot)
                page_text = (await page.evaluate('document.body.innerText')).strip() if page else ''
                result = {
                    'success': True,
                    'screenshot': screenshot_b64,
                    'screenshot_path': path.replace('/workspace/', ''),
                    'page_text': (page_text or '')[:5000],
                }
            
            elif action == 'get_content':
                selector = args.get('selector', '')
                if selector:
                    text = await page.text_content(selector)
                else:
                    text = await page.evaluate('document.body.innerText')
                result = {'success': True, 'content': (text or '')[:5000], 'url': page.url}
            
            elif action == 'get_page_structure':
                await page.wait_for_load_state('domcontentloaded')
                await asyncio.sleep(0.4)
                # Return richer control metadata so LLM can choose type/click/select correctly.
                elements = await page.evaluate("""() => {
                    const out = [];
                    const asSelector = (el) => {
                        if (el.id) return '#' + CSS.escape(el.id);
                        if (el.name && /^(input|textarea|select)$/i.test(el.tagName)) {
                            return el.tagName.toLowerCase() + '[name="' + (el.name || '').replace(/"/g, '\\\\"') + '"]';
                        }
                        const dt = el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-qa');
                        if (dt) return '[data-testid="' + dt.replace(/"/g, '\\\\"') + '"]';
                        if (el.type && el.tagName === 'INPUT') return 'input[type="' + el.type + '"]';
                        return el.tagName.toLowerCase();
                    };
                    const visible = (el) => !!(el.offsetParent || (el.getClientRects && el.getClientRects().length));
                    const roleOf = (el) => ((el.getAttribute('role') || '') + '').toLowerCase();
                    const textOf = (el) => ((el.value !== undefined ? el.value : el.innerText) || '').trim();

                    document.querySelectorAll('input, textarea, select, button, [role="button"], [role="combobox"], [role="listbox"], a[href]').forEach(el => {
                        if (!visible(el)) return;
                        const tag = (el.tagName || '').toLowerCase();
                        const role = roleOf(el);
                        const type = ((el.type || '') + '').toLowerCase();
                        const isSelect = tag === 'select';
                        const isNativeMultiSelect = isSelect && !!el.multiple;
                        const isRoleCombo = role === 'combobox';
                        const isRoleListbox = role === 'listbox';
                        const isAriaMultiSelect = (isRoleCombo || isRoleListbox) && (((el.getAttribute('aria-multiselectable') || '') + '').toLowerCase() === 'true');
                        const optionsCount = isSelect ? el.options.length : Number(el.getAttribute('aria-setsize') || 0) || null;

                        out.push({
                            kind: tag === 'a' ? 'link' : tag,
                            selector: asSelector(el),
                            tag,
                            role: role || null,
                            control_type: isNativeMultiSelect || isAriaMultiSelect ? 'multiselect' : (isSelect || isRoleCombo || isRoleListbox ? 'select' : 'input'),
                            type: type || null,
                            name: el.name || null,
                            placeholder: (el.placeholder || '').slice(0, 60) || null,
                            label: (el.getAttribute('aria-label') || el.title || (el.closest('label') && el.closest('label').innerText) || '').trim().slice(0, 80),
                            text: textOf(el).slice(0, 60) || null,
                            is_select: isSelect || isRoleCombo || isRoleListbox,
                            is_multiselect: isNativeMultiSelect || isAriaMultiSelect,
                            options_count: optionsCount,
                        });
                    });
                    return out.slice(0, 60);
                }""")
                result = {'success': True, 'elements': elements, 'url': page.url}
            
            elif action == 'wait':
                selector = args.get('selector', '')
                timeout = args.get('timeout', 5000)
                await page.wait_for_selector(selector, timeout=timeout)
                result = {'success': True, 'found': selector}
            
            elif action == 'get_console_logs':
                log_entries = []
                try:
                    cdp = await page.context.new_cdp_session(page)
                    def on_log(ev):
                        entry = ev.get('entry', ev) if isinstance(ev, dict) else {}
                        log_entries.append({
                            'level': entry.get('level', ''),
                            'text': entry.get('text', str(entry))[:500],
                            'url': entry.get('url', '')[:200],
                        })
                    cdp.on('Log.entryAdded', on_log)
                    await cdp.send('Log.enable')
                    await asyncio.sleep(0.5)
                except Exception as e:
                    result = {'success': False, 'error': str(e), 'url': page.url}
                else:
                    result = {'success': True, 'logs': log_entries[:50], 'url': page.url}
            
            elif action == 'get_network_failures':
                request_failures = []
                bad_responses = []
                def on_fail(req):
                    request_failures.append({'url': req.url[:300], 'failure': (getattr(req, 'failure', None) or 'request failed')})
                def on_resp(resp):
                    if resp.status >= 400:
                        bad_responses.append({'url': resp.url[:300], 'status': resp.status})
                page.on('requestfailed', on_fail)
                page.on('response', on_resp)
                await asyncio.sleep(2)
                result = {
                    'success': True,
                    'request_failures': request_failures[:30],
                    'bad_status_responses': bad_responses[:30],
                    'url': page.url,
                }
            
            elif action == 'execute_script':
                script = args.get('script', '')
                try:
                    eval_result = await page.evaluate(script)
                    result = {'success': True, 'url': page.url, 'result': str(eval_result) if eval_result is not None else 'ok'}
                except Exception as e:
                    result = {'success': False, 'error': str(e), 'url': page.url}
            
            elif action == 'scroll':
                direction = (args.get('direction') or 'down').lower()
                amount = int(args.get('amount') or 500)
                to_bottom = args.get('to_bottom', False)
                try:
                    if to_bottom:
                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    else:
                        delta = amount if direction == 'down' else -amount
                        await page.evaluate(f'window.scrollBy(0, {delta})')
                    result = {'success': True, 'url': page.url, 'scrolled': 'bottom' if to_bottom else direction}
                except Exception as e:
                    result = {'success': False, 'error': str(e), 'url': page.url}
            
            else:
                result = {'success': False, 'error': f'Unknown action: {action}'}

            result = await attach_failure_artifacts(result)
            
            print(json.dumps(result))
        
        except Exception as e:
            print(json.dumps({'success': False, 'error': str(e)}))
        
        # DON'T close browser — keep it running for next action!
        # Only disconnect the CDP connection
        if connected_to_existing:
            pass  # Don't close, just let the script end

if __name__ == '__main__':
    asyncio.run(main())
'''


class BrowserTools:
    """Browser automation tools using Playwright in the sandbox."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        # Cache browser readiness checks to avoid expensive ping+exec on every action.
        self._last_browser_check_ts = 0.0
        self._browser_check_ttl_sec = 15.0
        self._browser_action_script_ready = False

    def _should_check_browser(self) -> bool:
        """Return True when it's time to re-check keepalive browser availability."""
        return (time.monotonic() - self._last_browser_check_ts) >= self._browser_check_ttl_sec

    def _mark_browser_checked(self) -> None:
        """Record the last moment browser keepalive was checked/launched."""
        self._last_browser_check_ts = time.monotonic()

    async def _ensure_browser_running(self, force: bool = False) -> None:
        """Запустить в контейнере постоянный процесс с браузером (если ещё не запущен), чтобы окно было видно в noVNC."""
        if not force and not self._should_check_browser():
            return

        await docker_manager.write_file(self.project_id, "/tmp/browser_ping.py", BROWSER_PING_SCRIPT)
        ping = await docker_manager.exec_command(
            self.project_id, "python3 /tmp/browser_ping.py", workdir="/workspace", timeout=5
        )
        if ping.get("stdout", "").strip() == "OK":
            self._mark_browser_checked()
            return
        await docker_manager.write_file(self.project_id, "/tmp/browser_keepalive.py", BROWSER_KEEPALIVE_SCRIPT)
        await docker_manager.exec_command(
            self.project_id,
            "sh -c 'nohup python3 /tmp/browser_keepalive.py </dev/null >/tmp/browser_keepalive.log 2>&1 &'",
            workdir="/workspace",
            timeout=5,
        )
        await asyncio.sleep(3)
        self._mark_browser_checked()

    def _log_args(self, args: dict, max_val_len: int = 80) -> dict:
        """Сокращённые аргументы для логов (без огромных content)."""
        out = {}
        for k, v in (args or {}).items():
            if k == "content" and isinstance(v, str) and len(v) > max_val_len:
                out[k] = v[:max_val_len] + f"...({len(v)} chars)"
            elif isinstance(v, list) and len(v) > 3:
                out[k] = f"list[{len(v)} items]"
            elif isinstance(v, str) and len(v) > max_val_len:
                out[k] = v[:max_val_len] + "..."
            else:
                out[k] = v
        return out

    async def _ensure_action_script(self, force: bool = False) -> None:
        """Write browser action script once per engine instance (or force rewrite)."""
        if self._browser_action_script_ready and not force:
            return
        await docker_manager.write_file(
            self.project_id, "/tmp/browser_action.py", BROWSER_ACTION_SCRIPT
        )
        self._browser_action_script_ready = True

    async def _execute_browser_script(self, action: str, args: dict) -> dict:
        """Execute Playwright script in the sandbox."""
        await self._ensure_browser_running()
        print(f"[Browser] project={self.project_id} action={action} args={self._log_args(args)}")
        await self._ensure_action_script()
        args_json = json.dumps(args, ensure_ascii=False)
        await docker_manager.write_file(
            self.project_id, "/tmp/browser_action_args.json", args_json
        )
        command = f"python3 /tmp/browser_action.py {action} /tmp/browser_action_args.json"
        result = await docker_manager.exec_command(
            self.project_id,
            command,
            workdir="/workspace",
            timeout=60,
        )

        if not result["success"]:
            stderr_full = result.get("stderr", "") or ""
            err = stderr_full[:200] if stderr_full else "Unknown error"
            print(f"[Browser] action={action} result=error stderr={err}")

            # If browser died while TTL window is active, force refresh once and retry action.
            browser_down_markers = (
                "connect_over_cdp",
                "ECONNREFUSED",
                "Target page, context or browser has been closed",
                "BrowserType.connect_over_cdp",
            )
            if any(marker in stderr_full for marker in browser_down_markers):
                print(f"[Browser] action={action} detected stale browser, forcing keepalive check + retry")
                await self._ensure_browser_running(force=True)
                result = await docker_manager.exec_command(
                    self.project_id,
                    command,
                    workdir="/workspace",
                    timeout=60,
                )
                if not result["success"]:
                    retry_err = (result.get("stderr", "") or "Unknown error")[:200]
                    print(f"[Browser] action={action} retry_failed stderr={retry_err}")
                    return {"success": False, "error": result.get("stderr", "Unknown error")}
            elif "can't open file '/tmp/browser_action.py'" in stderr_full or "No such file or directory" in stderr_full:
                print(f"[Browser] action={action} action script missing, forcing rewrite + retry")
                await self._ensure_action_script(force=True)
                result = await docker_manager.exec_command(
                    self.project_id,
                    command,
                    workdir="/workspace",
                    timeout=60,
                )
                if not result["success"]:
                    retry_err = (result.get("stderr", "") or "Unknown error")[:200]
                    print(f"[Browser] action={action} retry_failed after script rewrite stderr={retry_err}")
                    return {"success": False, "error": result.get("stderr", "Unknown error")}
            else:
                return {"success": False, "error": result.get("stderr", "Unknown error")}

        # Parse JSON output
        try:
            output = (result.get("stdout") or "").strip()
            if not output:
                print(f"[Browser] action={action} empty stdout")
                return {"success": False, "error": "Browser script returned no output"}

            out = None
            # Browser may print warnings/log lines before JSON.
            # Parse from the end line-by-line and pick the last valid JSON object.
            for line in reversed(output.splitlines()):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    out = candidate
                    break

            if out is None:
                # Fallback: try parsing the whole stdout as JSON
                out = json.loads(output)

            url = out.get("url", "")
            success = out.get("success", False)
            err = out.get("error", "")
            extra = f" url={url[:60]}" if url else ""
            if err:
                print(f"[Browser] action={action} success={success} error={str(err)[:120]}")
            else:
                print(f"[Browser] action={action} success={success}{extra}")
            return out
        except json.JSONDecodeError as e:
            print(f"[Browser] action={action} parse_error={e}")
            return {
                "success": False,
                "error": f"Failed to parse result: {e}. Output: {result['stdout'][:500]}",
            }

    async def navigate(self, url: str, timeout: int = 30000) -> dict:
        """Navigate to URL."""
        return await self._execute_browser_script("navigate", {"url": url, "timeout": timeout})

    async def click(self, selector: str, timeout: int = 5000) -> dict:
        """Click on element."""
        return await self._execute_browser_script("click", {"selector": selector, "timeout": timeout})

    async def type_text(self, selector: str, text: str, timeout: int = 5000) -> dict:
        """Type text into input."""
        return await self._execute_browser_script("type", {"selector": selector, "text": text, "timeout": timeout})

    async def select_option(self, selector: str, value: str = "", label: str = "") -> dict:
        """Select option in a <select> by value or label."""
        args = {"selector": selector}
        if value:
            args["value"] = value
        if label:
            args["label"] = label
        return await self._execute_browser_script("select", args)

    async def screenshot(self, full_page: bool = False) -> dict:
        """Take screenshot."""
        return await self._execute_browser_script("screenshot", {"full_page": full_page})

    async def get_content(self, selector: str = "") -> dict:
        """Get page or element content."""
        return await self._execute_browser_script("get_content", {"selector": selector})

    async def get_page_structure(self) -> dict:
        """Get map of interactive elements (inputs, buttons) with selectors for fast testing."""
        return await self._execute_browser_script("get_page_structure", {})

    async def fill_form(self, steps: list, submit_selector: str = "") -> dict:
        """Fill multiple fields and optionally click submit in one run (faster than type+type+click)."""
        return await self._execute_browser_script(
            "fill_form", {"steps": steps, "submit_selector": submit_selector or ""}
        )

    async def wait(self, selector: str, timeout: int = 5000) -> dict:
        """Wait for element."""
        return await self._execute_browser_script("wait", {"selector": selector, "timeout": timeout})

    async def get_console_logs(self) -> dict:
        """Get browser console logs (JS errors, console.log, etc.) via CDP. Call after opening a page to debug."""
        return await self._execute_browser_script("get_console_logs", {})

    async def get_network_failures(self) -> dict:
        """Get failed requests and responses with status 4xx/5xx. Listens for 2s after call. Use after navigate to debug."""
        return await self._execute_browser_script("get_network_failures", {})

    async def execute_script(self, script: str) -> dict:
        """Run JavaScript in the page (e.g. window.scrollTo(0, document.body.scrollHeight) to scroll to bottom)."""
        return await self._execute_browser_script("execute_script", {"script": script})

    async def scroll(self, direction: str = "down", amount: int = 500, to_bottom: bool = False) -> dict:
        """Scroll the page: direction 'down'/'up' by amount pixels, or to_bottom=True to scroll to the very bottom."""
        return await self._execute_browser_script(
            "scroll", {"direction": direction, "amount": amount, "to_bottom": to_bottom}
        )
