from __future__ import annotations

from pathlib import Path
import requests

INVENTORY = "487547"
ROOT = Path("/tmp/filinkov_ready") / INVENTORY
TIMEOUT = 60

URLS = [
    "https://uc7c00911920002b0db6edc3e080.dl.dropboxusercontent.com/cd/0/get/DIYrHvn8N_H8ybNwFVWHZD3snY43xR2nmgRxUrNHNddSz-fdzjHALAVI7KSQbWqZCc-6_dXFqYhobJW0hpjnXeYkMqZk4STD7-9qWoMwSvW6QYaAoAO7jCgFV4HZswVjJAO9wzOtnO8JfePxMiMSHF3PNh9NMOB6cDb5BWSRh-Q2pA/file?c_luid=6799959c",
    "https://uc4680b1897aa05eb58cc5e43b53.dl.dropboxusercontent.com/cd/0/get/DIYWxiWJGGNUs0IHj1dNQJ8jUIleOA3zfm83iWgivekZecR7xUgViE58XsLyaoLF8zbiepabn-LsonK2LBhDkxvv7WIMZQ7X7js97GKtjZXG3M2z6qrvTNA8szm73rRMmega7gKxFtkOMgVTXtPGPA2_VYFq8JIvoY-GaJgtAUG_6A/file?c_luid=6799959c",
    "https://ucb9699886d025397abdcaa5ac89.dl.dropboxusercontent.com/cd/0/get/DIaaTHIT6Vx1iqp11fBpH1C-FHkMlfaGTEzw5ZAkeZ2ukCszgJkFVe3TuCQ3TT6ymXddvRJELlGAyA2fJXcXrmQRMuIQmdfTDxsXHEvXsO4LAzPKkh6J9SLzBpw7Cuc8TX4GJ4QdOX1JzgbxI8iLqHIEuz6sK6OSmPEdUAPDAFzzZg/file?c_luid=6799959c",
    "https://ucf156c5d0d8e0e85952a96930aa.dl.dropboxusercontent.com/cd/0/get/DIZN-WI9jJpkDKF0eQ99JFYhyuqdRAFfgEMMaL2TxBwdQMkxfMXdK_01tX_1C_OBk6Ac2MVYJ_r4NHj6MTckbq5wkh5WFOAhTZlJ1RCsKy0uk76BQUlpIqIxM5T_4fXVD4274w32Tt1oS8c-BoldCWOPBkPzZd-iAutVckj58R_tRQ/file?c_luid=6799959c",
    "https://ucd1645349bc2670bf33656c0d79.dl.dropboxusercontent.com/cd/0/get/DIY1fqAorNQtfvidT95o6N5FW-wR7uHvx3cNcyD2ypi4GsVvlM6t3jMoCV6muxf06l2TNoUn16YD-SCeFKlvxPB3B7b20GEwJoRtZOplfLFvHS855HvVBQlEBbAb88QrK7hku6WHCdezMXtERDThxtG_vLS7mR31vZw_owZ3Z8l-Zg/file?c_luid=6799959c",
    "https://ucc41dedc1f83143ebc9bc0383ca.dl.dropboxusercontent.com/cd/0/get/DIZgTcegRjSPn_pHuLHi6giqZ1XXAvpy3vE_dM1WVDdwX1e4fzgCfXjfbldC47sn23arSJRrY3rV0vaN1xht3Zd37rr5kdC7VGRhBSPeio0j4jlQq-kmfJKed_8AffMqGOnQTE63blPg-wx5DCpje2C-cdTHWvV7dem8ia6yJEMJSA/file?c_luid=6799959c",
    "https://uce839c748688783f7856fe8ecec.dl.dropboxusercontent.com/cd/0/get/DIZYHhVliBcuaJTvNgZMQ_OHuM1klLlzDkT1J-SGMyJ-bhJcd5hjVCGxSKo6ElRugbe9B5r9RbnOIqivLRQTtrUzkvM7tcH9k5N0VPqEtnmjBrT8_GqmTNMcrTqgi7a8TkNz5yAQgi46fvLdkjrQFACO-a3-RunCwuPSel3kSJcsUw/file?c_luid=6799959c",
    "https://uca5f5aa34a70d90cb7899dd05ac.dl.dropboxusercontent.com/cd/0/get/DIbDPAI7YA6EwboShxx1cyonZxBcpzD9oQHe3ggQlU_pGpKyJVwmhLXOOaTiO6cnChljoiw3NaKt3g0i92vaU2OnqdipceNHhk6DvYlsAzfKMJB5whxU-n130pTeLByjuyCzV1dmn3xDNlw9pQL39T6kL7-WKUUHV6hzAqJ__1Kryw/file?c_luid=6799959c",
    "https://uc597a853f015a16cfb31a086cd5.dl.dropboxusercontent.com/cd/0/get/DIYASWsnf3obOsYQ1uGG4DSbTYAmEyE1e4ryjnVLphfc-fu63vnl8FRMvYmXIugY5QNr25XLBrufHFbPpOwxQAxt-lMSXk6XRF193oTj7T0f-OpKNm1hJH8hD-LNUJjFCX0NbsTCDrjRNz1D1Yaiay7LbKmx8OQv0iM_3aH-vpqXUQ/file?c_luid=6799959c",
    "https://uc01cfb71b6b15d99a54f1eba6bf.dl.dropboxusercontent.com/cd/0/get/DIbR2DN_buVej_crNUxeZzEEzHB1pZQ_MCQC2dnY1vPu3BFtvORSrKhxmBEEG7cY6zduCIySMzSum_7arySsxtksz1K_cY4W-MIDtzqDnS_30yZlXiT-VviPYyryCHAY7m4iyB4uoWStu8yDLEiYpljJbP-bi8sNPSgvqM2cwbTVzA/file?c_luid=6799959c",
]


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(URLS, 1):
        target = ROOT / f"{i:02d}.jpg"
        if target.exists() and target.stat().st_size > 1000:
            continue
        with requests.get(url, timeout=TIMEOUT, stream=True, headers={"User-Agent": "FILINKOV-AUTO-final-stage/1.0"}) as resp:
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").lower()
            if "image" not in ctype:
                raise RuntimeError(f"slot {i}: not an image ({ctype})")
            raw = resp.content
        if len(raw) < 1000:
            raise RuntimeError(f"slot {i}: image too small")
        target.write_bytes(raw)
        print(f"Q2_FINAL_SLOT {i:02d}/10 {len(raw)} bytes", flush=True)
    ready = [p for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}]
    print(f"Q2_FINAL_STAGED {len(ready)}/10", flush=True)
    return 0 if len(ready) >= 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
