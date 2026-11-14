"""完整自动化注册 + mail.tm 临时邮箱 + 批量循环 + 兑换流程示例。"""

import json
import os
import sys
import time
import uuid
import re
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from musepy import caa, falco
from musepy.client import MuseClient, MuseError, random_dob

MAIL_API = "https://api.mail.tm"


def load_config(path="config.json"):
    if not os.path.exists(path):
        raise SystemExit(f"缺少配置文件 {path}，请从 config.example.json 复制并填写")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def create_temp_email():
    """通过 mail.tm 自动创建临时邮箱并返回邮箱地址与 mail token"""
    print("  [0] 正在通过 mail.tm 创建临时邮箱...")
    domains_res = requests.get(f"{MAIL_API}/domains").json()
    domain = domains_res["hydra:member"][0]["domain"]
    
    email = f"muse_{int(time.time())}_{uuid.uuid4().hex[:6]}@{domain}"
    password = "StrongPassword123!"
    
    reg_res = requests.post(f"{MAIL_API}/accounts", json={
        "address": email,
        "password": password
    })
    if reg_res.status_code not in [200, 201]:
        raise Exception(f"创建临时邮箱失败: {reg_res.text}")
        
    token_res = requests.post(f"{MAIL_API}/token", json={
        "address": email,
        "password": password
    }).json()
    
    print(f"      临时邮箱: {email}")
    return email, token_res["token"]


def fetch_otp_from_mail(mail_token):
    """自动轮询 mail.tm 收件箱获取 muse.ai 的验证码"""
    print("  [3.1] 等待接收验证码 (Polling mail.tm inbox)...")
    headers = {"Authorization": f"Bearer {mail_token}"}
    
    for attempt in range(15):
        time.sleep(2)
        try:
            messages_res = requests.get(f"{MAIL_API}/messages", headers=headers).json()
            messages = messages_res.get("hydra:member", [])
            
            if messages:
                msg_id = messages[0]["id"]
                msg_detail = requests.get(f"{MAIL_API}/messages/{msg_id}", headers=headers).json()
                
                content = msg_detail.get("text", "") or msg_detail.get("intro", "")
                match = re.search(r'\b\d{6}\b', content)
                if match:
                    otp_code = match.group(0)
                    print(f"      成功获取验证码: {otp_code}")
                    return otp_code
        except Exception as e:
            continue
            
    raise Exception("获取验证码超时：未能在收件箱中找到 OTP 邮件")


def register_single_account(cfg, index, total):
    """执行单个账号的完整注册与兑换流程"""
    print(f"\n--- [Mulai] Proses Akun ke-{index} dari {total} ---")
    
    proxy = cfg.get("proxy") or None
    invite_code = cfg.get("invite_code") or ""

    # 1. Buat email sementara otomatis
    try:
        email, mail_token = create_temp_email()
    except Exception as e:
        print(f"  [✗] Gagal membuat email sementara: {e}")
        return False

    c = MuseClient(proxy=proxy, impersonate=cfg.get("impersonate", "chrome131"),
                   lang=cfg.get("lang", "ja"))

    try:
        print(f"  [1] 打开首页  {email}")
        c.open_home()

        print("  [2] 重启注册流程")
        c.restart()

        funnel = caa.Caa(c.fp, c.lang)
        if cfg.get("send_caa", True):
            funnel.page_shown(c.s)
            time.sleep(1)
            funnel.focus(c.s)
            time.sleep(0.3)
            funnel.continue_clicked(c.s)
            funnel.registration_start(c.s)

        print("  [3] 发送验证码")
        c.send_otp(email)

        # Ambil OTP otomatis
        code = fetch_otp_from_mail(mail_token)

        if cfg.get("send_caa", True):
            funnel.otp_typed(c.s)

        print("  [4] 校验验证码")
        c.confirm_otp(code)

        first = cfg.get("first_name") or "三郎"
        last = cfg.get("last_name") or "近藤"
        dob = cfg.get("date_of_birth") or random_dob()

        if cfg.get("send_caa", True):
            funnel.otp_success(c.s)
            funnel.birthday_start(c.s)
            time.sleep(2)
            funnel.birthday_next(c.s)

        print(f"  [5] 完成注册  {last} {first}  {dob}")
        data = c.register_completion(dob, first, last)
        uid = (data.get("auth_meta_session") or {}).get("frl_account_id")
        print(f"      account_id = {uid}")

        if cfg.get("send_caa", True):
            funnel.create_success(c.s, uid)
            funnel.login_success(c.s, uid)

        if cfg.get("send_falco", True):
            falco.send(c.s, c.fp, c.lang, str(uuid.uuid4()), [
                falco.perf("https://muse.ai/"),
                falco.interaction("https://muse.ai/", "page_load", "main_app", "page_load"),
            ])

        print("  [6] 激活会话（accept-tos）")
        for _ in range(2):
            c.accept_tos()
            if c.is_live():
                break
            time.sleep(1.2)
        print(f"      invite = {'200 活号' if c.is_live() else '401/403 无 hatch 权限'}")

        print("  [7] 唤醒 VM")
        vm = c.wake_vm()
        print(f"      vm_id = {vm}")

        c.stateful_sharing()

        own_code = None
        print(f"  [8] 兑换邀请码  {invite_code or '(未配置)'}")
        if invite_code:
            ok, reason = c.redeem(invite_code)
            print(f"      redeem = {ok} / {reason}")
            if ok:
                own_code = c.scratch_code()
                print(f"  [9] 自己的邀请码 = {own_code}")
            else:
                print("      失败提示: already_redeemed=已用过 | used_up=达上限 | invalid=码不存在")
        else:
            print("      跳过兑换")

        sess = c.session_cookie()
        
        # Simpan hasil akun ke accounts.json
        account_data = {
            "email": email,
            "mail_password": "StrongPassword123!",
            "account_id": uid,
            "vm_id": vm,
            "own_invite_code": own_code,
            "hatch_sess": sess,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        output_file = "accounts.json"
        existing_accounts = []
        if os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8") as f:
                try:
                    existing_accounts = json.load(f)
                except json.JSONDecodeError:
                    existing_accounts = []
        existing_accounts.append(account_data)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(existing_accounts, f, indent=2, ensure_ascii=False)
            
        print(f"  [✓] Akun ke-{index} berhasil dibuat dan disimpan ke {output_file}")
        return True

    except Exception as e:
        print(f"  [✗] Protocol Error pada akun ke-{index}: {e}")
        return False
    finally:
        c.close()


def main():
    cfg = load_config()
    
    print("=" * 50)
    print("      MUSE.AI AUTO REGISTER & REDEEM BOT")
    print("=" * 50)
    
    try:
        total_str = input("Masukkan jumlah akun yang ingin dibuat: ").strip()
        total_accounts = int(total_str)
        if total_accounts <= 0:
            print("Jumlah akun harus lebih besar dari 0.")
            return
    except ValueError:
        print("Input tidak valid. Harap masukkan angka.")
        return

    success_count = 0
    for i in range(1, total_accounts + 1):
        success = register_single_account(cfg, i, total_accounts)
        if success:
            success_count += 1
        
        # Jeda antar pembuatan akun agar tidak terkena rate limit
        if i < total_accounts:
            print("\nMenunggu 3 detik sebelum melanjutkan akun berikutnya...")
            time.sleep(3)

    print("\n" + "=" * 50)
    print(f" Selesai! Berhasil membuat {success_count} dari {total_accounts} akun.")
    print(" Semua hasil akun tersimpan rapi di file: accounts.json")
    print("=" * 50)


if __name__ == "__main__":
    main()
