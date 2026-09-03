"""Cài gói SPONSORED (vnstock_data) HEADLESS trên server — không cần web UI.

Điều kiện: đã copy thư mục ~/.vnstock từ máy đã kích hoạt license sang server
(chứa api_key.json), và đã `pip install vnstock-installer` (requirements.txt).

Chạy trong venv của app:
    python sponsored_install.py                 # cài vnstock_data
    python sponsored_install.py vnstock_data vnstock_ta   # cài nhiều gói
"""

import json
import os
import sys
import tempfile


def main():
    packages = sys.argv[1:] or ["vnstock_data"]
    key_path = os.path.join(os.path.expanduser("~"), ".vnstock", "api_key.json")
    if not os.path.exists(key_path):
        print(f"❌ Không thấy {key_path} — copy thư mục ~/.vnstock từ máy đã kích hoạt sang trước.")
        sys.exit(1)
    with open(key_path, encoding="utf-8") as f:
        api_key = json.load(f)["api_key"]

    from vnstock_installer import config as icfg
    from vnstock_installer.api import VnstockAPIClient
    from vnstock_installer.installer import VnstockInstaller

    client = VnstockAPIClient(api_key, python_executable=sys.executable)
    print("→ Đăng ký thiết bị với server vnstocks…")
    client.refresh_device_id()
    ok, msg, info = client.register_device()
    print(("✅" if ok else "❌"), msg)
    if not ok:
        sys.exit(1)
    client.save_api_key()

    ok, pkgs = client.list_available_packages()
    if ok:
        try:
            names = [p.get("name", p) if isinstance(p, dict) else p for p in pkgs]
            print("→ Gói khả dụng theo license:", names)
        except Exception:
            print("→ Gói khả dụng:", pkgs)

    icfg.temporary_directory = tempfile.mkdtemp()
    inst = VnstockInstaller(client, sys.executable,
                            use_venv=True, venv_path=sys.prefix)
    print("→ Bootstrap vnai…")
    print(inst.bootstrap_vnai())
    for name in packages:
        print(f"→ Cài {name}…")
        ok, msg = inst.install_package(name)
        print(("✅" if ok else "❌"), name, "—", msg)
        if not ok:
            sys.exit(1)
    client.save_installation_info()
    print("\n✅ XONG — kiểm tra:  python -c \"import vnstock_data; print('ok')\"")
    print("   rồi chạy tiếp:     python deploy_check.py --ping")


if __name__ == "__main__":
    main()
