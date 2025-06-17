from __future__ import annotations

"""
Selenium (undetected‑chromedriver) tabanlı hızlı ve stabil toplu WhatsApp mesaj gönderici.

🔧 *Yeni Özellikler (v3.2 – "Restart Butonu" güncellemesi)*

Bu sürümde isteğin üzerine **tam sıfırlama** yapacak bir **Restart / Sıfırla** düğmesi

* **GUI tarafı**
  * Restart / Sıfırla adlı turuncu bir buton eklendi. Tıklandığında:
    * Tüm metin kutuları ve numara listesi temizlenir.
    * Saat/dakika Spinboxları ile aralık ve timeout alanları varsayılanlarına döner.
    * Mod seçimi tekrar *Anında* konuma alınır.
    * Aktif bir WebDriver (varsa) kapatılır; global _DRIVER sıfırlanır.
    * Gönderim butonu yeniden etkinleştirilir.

Diğer tüm işlevler **hiçbir değişiklik yapılmadan** korunmuştur.
"""

import sys
import threading
import time
import datetime
import urllib.parse
import os
from pathlib import Path
from typing import Optional, List, Tuple

# ------------------------------------------------------------
# 1) Opsiyonel GUI (Tkinter)
# ------------------------------------------------------------

try:
    import tkinter as tk
    from tkinter import messagebox

    GUI_AVAILABLE = True
except ModuleNotFoundError:
    GUI_AVAILABLE = False

    INSTALL_MSG = (
        "Tkinter (python3-tk) bulunamadı:\n"
        " • Windows: Python resmi kurulumunda yeniden kurulum yaparken ‘tcl/tk and IDLE’ kutusunu işaretleyin.\n"
        " • Debian/Ubuntu:  sudo apt-get install python3-tk\n"
        " • Fedora:         sudo dnf install python3-tkinter\n"
        "Veya GUI olmadan çalıştırmak için:  python bulk_whatsapp_sender_uc_v3.py --cli"
    )

# ------------------------------------------------------------
# 2) Selenium + undetected-chromedriver
# ------------------------------------------------------------
import os, sys, time, threading, urllib.parse, json   # ← json eklendi
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import timedelta

try:
    import undetected_chromedriver as uc
except ImportError:
    sys.exit("undetected-chromedriver yüklü değil.  →  pip install undetected-chromedriver")

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys          # ← eklendi (Enter yedeği)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ------------------------------------------------------------
# WebDriver (singleton)
# ------------------------------------------------------------
_DRIVER: Optional["uc.Chrome"] = None
_PROFILE: Optional[str] = None


def _profile_dir() -> Path:
    global _PROFILE
    if _PROFILE:
        return Path(_PROFILE)
    p = (Path(os.getenv("APPDATA") or Path.home()) / "whatsapp_profile").resolve()
    p.mkdir(parents=True, exist_ok=True)
    _PROFILE = str(p)
    return p


def _driver_alive(drv) -> bool:
    """Tarayıcı hâlâ açık mı?"""
    if not drv:
        return False
    try:
        _ = drv.current_url
        return True
    except Exception:
        return False


def _chrome_options() -> uc.ChromeOptions:
    """Tek bir yerde Chrome seçenekleri oluşturur."""
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={_profile_dir()}")

    # kaynak dostu ayarlar
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--remote-allow-origins=*")

    # oturum kurtarma / arka-plan modunu kapat
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-background-mode")
    opts.add_argument("--disable-features=SessionRestore,AutomaticTabDiscarding,BackgroundMode")
    return opts


def get_driver() -> uc.Chrome:
    """Chrome örneği oluşturur; kapanmışsa yeniden başlatır."""
    global _DRIVER

    if not _driver_alive(_DRIVER):
        _DRIVER = None

    if _DRIVER is None:
        _DRIVER = uc.Chrome(options=_chrome_options(), headless=False)
        _DRIVER.maximize_window()
        _DRIVER.get("https://web.whatsapp.com")  # ilk sefer QR gerekir

    return _DRIVER


def _close_driver():
    global _DRIVER
    if _driver_alive(_DRIVER):
        try:
            _DRIVER.quit()
            # ek güvenlik: servis sürecini öldür
            if hasattr(_DRIVER, "service") and _DRIVER.service.process:
                _DRIVER.service.process.kill()
        except Exception:
            pass
    _DRIVER = None
# ------------------------------------------------------------
# 2-b) Kalıcı Rehber Yardımcıları
# ------------------------------------------------------------
CONTACTS_FILE = _profile_dir() / "contacts.json"


def load_contacts() -> list[str]:
    """contacts.json → ['905551112233', ...]"""
    try:
        with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_contacts(lst: list[str]):
    """Yinelenenleri at, sıralı kaydet."""
    CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(set(lst)), f, ensure_ascii=False, indent=2)

# ------------------------------------------
# 3) Gönderim yardımcıları
# ------------------------------------------------------------
MSG_BOX = (By.CSS_SELECTOR, "div[role='textbox'][contenteditable='true']")

SEND_ICON = (
    By.CSS_SELECTOR,
    "span[data-icon='send'], span[data-testid='send'], "
    "div[data-testid='send'], button[aria-label='Send']",
)


def _open_chat(number: str, message: str):
    """Numarayı & mesajı URL ile aç; kutu DOM’a düşene dek bekle."""
    drv = get_driver()

    if "web.whatsapp.com" not in drv.current_url:
        drv.get("https://web.whatsapp.com")

    url = (
        "https://web.whatsapp.com/send?phone="
        + number.lstrip("+") +
        "&text=" + urllib.parse.quote(message) +
        "&type=phone_number&app_absent=0" +
        f"&cb={int(time.time()*1000)}"          # cache-buster → her çağrı benzersiz
    )
    drv.get(url)

    WebDriverWait(drv, 4).until(EC.presence_of_element_located(MSG_BOX))


def _wait_and_send(timeout: int = 8) -> bool:
    """Gönder ikonu veya Enter tuşu ile mesajı iletir."""
    drv = get_driver()
    deadline = time.time() + timeout

    # 1) JavaScript ile ikon göründüğü anda tıkla (≈ 0,3 s)
    js_click = """
        const b = document.querySelector(
          "span[data-icon='send'],span[data-testid='send'],\
           div[data-testid='send'],button[aria-label='Send']"
        );
        if (b){ b.click(); return true; }
        return false;
    """
    while time.time() < deadline:
        try:
            if drv.execute_script(js_click):
                return True
        except Exception:
            pass
        time.sleep(0.05)          # 50 ms döngü

    # 2) Yedek plan: aktif elemana Enter
    try:
        drv.switch_to.active_element.send_keys(Keys.ENTER)
        return True
    except Exception:
        return False


def send_single(number: str, message: str, wait_sec: int, gap_sec: float):
    _open_chat(number, message)
    ok = _wait_and_send(wait_sec)
    print(("Gönderildi" if ok else "HATA") + f" → {number}")
    time.sleep(gap_sec)


def send_bulk(numbers: List[str], message: str, wait_sec: int, gap_sec: float):
    for num in numbers:
        send_single(num, message, wait_sec, gap_sec)


# ------------------------------------------------------------
# 3-b) Zamanlanmış çoklu mesaj yardımcısı
# ------------------------------------------------------------
def schedule_multiple_messages(
    numbers: List[str],
    msgs: List[Tuple[str, int, int, int, int, int]],
    gap_sec: int,
    wait_sec: int,
):
    """
    msgs → [(mesaj, yıl, ay, gün, saat, dakika), ...]
    Verilen her tarih-saatte send_bulk() tetikler.
    """
    import datetime as dt

    def _job(msg_text: str):
        send_bulk(numbers, msg_text, wait_sec, gap_sec)

    for txt, y, mo, d, h, mi in msgs:
        run_at = dt.datetime(y, mo, d, h, mi, 0)
        delay  = max((run_at - dt.datetime.now()).total_seconds(), 0)

        t = threading.Timer(delay, _job, args=(txt,))
        t.daemon = True            # bekçi thread → süreç bitince kapanır
        t.start()
# ------------------------------------------------------------
# 4) CLI modu (GUI yoksa --cli ile)
# ------------------------------------------------------------
def multiline_input(prompt: str, paragraphs: int = 2) -> str:
    """Konsolda çok satırlı metin okur. Boş satır paragraf sonu demektir."""
    print(f"{prompt} (her paragrafı boş satırla bitir):")
    paras: List[str] = []
    buf: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            if buf:                      # paragraf bitti
                paras.append("\n".join(buf).strip())
                buf = []
                if len(paras) == paragraphs:
                    break
        else:
            buf.append(line)
    if buf:                              # elde kalan satırlar
        paras.append("\n".join(buf).strip())
    return "\n\n".join(paras)


def cli_mode():
    print("=== WhatsApp Toplu Mesaj Botu – CLI (v3.2) ===")

    numbers = [
        n.strip().lstrip("+")
        for n in input("Alıcı numaraları (virgülle): ").split(",")
        if n.strip()
    ]

    msg1 = multiline_input("1. Mesaj")
    if not numbers or not msg1:
        sys.exit("Numara ve 1. mesaj zorunlu.")

    # 2. mesaj (opsiyonel)
    choice2 = input("2. mesaj da göndermek ister misin? (y/n): ").strip().lower()
    msg2, time2 = "", ""
    if choice2 == "y":
        msg2  = multiline_input("2. Mesaj")
        time2 = input("2. Mesaj saati (HH:MM): ").strip()

    # 3. mesaj (opsiyonel)
    choice3 = input("3. mesaj da göndermek ister misin? (y/n): ").strip().lower()
    msg3, time3 = "", ""
    if choice3 == "y":
        msg3  = multiline_input("3. Mesaj")
        time3 = input("3. Mesaj saati (HH:MM): ").strip()

    mode   = input("Mod? instantly / scheduled (i/s): ").strip().lower()
    wait_s = int(input("Gönder butonu timeout (sn) [10]: ") or 10)

    # ----------------------------- ANINDA MOD -----------------------------
    if mode == "a":
        gap_s = float(input("Mesajlar arası saniye [1]: ") or 1)

        for m in (msg1, msg2, msg3):          # 1-2-3 mesajı ardışık gönder
            if m.strip():
                send_bulk(numbers, m, wait_s, gap_s)

        _close_driver()
        os._exit(0)                           # süreçten kesin çıkış

    # -------------------------- ZAMANLANMIŞ MOD ---------------------------
    else:
        # 1) Date
        date_str = input("Gönderim tarihi (YYYY-MM-DD): ").strip()
        try:
            year, month, day = map(int, date_str.split("-"))
        except ValueError:
            sys.exit("Tarih formatı geçersiz (YYYY-MM-DD olmalı).")

        # 2) 1. Message time
        t1 = input("1. Mesaj saati (HH:MM): ").strip()
        h1, m1 = map(int, t1.split(":"))

        # 3) Wait time between messages
        gap_s = int(input("Mesajlar arası saniye (1-60) [5]: ") or 5)

        # 4) Messages list: (mesaj, Y, A, G, H, M)
        msgs: List[Tuple[str, int, int, int, int, int]] = [
            (msg1, year, month, day, h1, m1)
        ]
        if msg2 and time2:
            h2, m2 = map(int, time2.split(":"))
            msgs.append((msg2, year, month, day, h2, m2))
        if msg3 and time3:
            h3, m3 = map(int, time3.split(":"))
            msgs.append((msg3, year, month, day, h3, m3))

        # 5) Timers
        schedule_multiple_messages(numbers, msgs, gap_s, wait_s)
        print("Timers started…  (Ctrl+C ile çık)")

        # 6) Canlı non-daemon thread kalmayana kadar bekle
        while threading.active_count() > 1:
            time.sleep(1)

        _close_driver()
        os._exit(0)

# 5) Tkinter GUI
# ------------------------------------------------------------
if GUI_AVAILABLE and "--cli" not in sys.argv:
    import threading, tkinter as tk
    from tkinter import messagebox
    from datetime import datetime

    root = tk.Tk()
    root.title("WhatsApp Toplu Mesaj Botu – GUI (v3.2)")
    root.geometry("900x650")
    root.resizable(False, False)

    # ==== Kaydırılabilir Canvas + Scrollbar =========================
    canvas = tk.Canvas(root, highlightthickness=0)
    vbar   = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)

    vbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    scroll_frame = tk.Frame(canvas)                       # <<< TÜM ARAYÜZ BURADA
    win_id = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

    # içerik büyüdükçe scrollregion’u güncelle
    def _on_frame_config(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
    scroll_frame.bind("<Configure>", _on_frame_config)

    # canvas genişleyince iç çerçevenin genişliğini eşitle
    def _on_canvas_config(event):
        canvas.itemconfigure(win_id, width=event.width)
    canvas.bind("<Configure>", _on_canvas_config)

    # fare tekerleği
    def _on_mousewheel(event):
        canvas.yview_scroll(-1 * (event.delta // 120), "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)
    canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
    canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll( 1, "units"))
    # ===============================================================

    # ------------ yardımcı toast (bloklamayan bilgi penceresi) -----
    def toast(msg: str, msec: int = 1500):
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        tk.Label(top, text=msg, bg="#ffffe0", relief="solid", bd=1)\
          .pack(ipadx=10, ipady=5)
        root.update_idletasks()
        x = root.winfo_x() + (root.winfo_width() // 2) - (top.winfo_reqwidth() // 2)
        y = root.winfo_y() + (root.winfo_height() // 2) - (top.winfo_reqheight() // 2)
        top.geometry(f"+{x}+{y}")
        top.after(msec, top.destroy)

    # --- REHBER BLOKU ---------------------------------------------
        # --- REHBER BLOKU ---------------------------------------------
    frm_book = tk.Frame(scroll_frame, padx=5, pady=5)
    frm_book.pack(anchor="w", fill="x")

    tk.Label(frm_book, text="Registered numbers:").grid(row=0, column=0, sticky="w")

    lst_contacts = tk.Listbox(frm_book, height=6, width=25)
    lst_contacts.grid(row=1, column=0, rowspan=3, padx=(0, 10))

    def _populate_contacts():
        lst_contacts.delete(0, tk.END)
        for num in load_contacts():
            lst_contacts.insert(tk.END, num)
    _populate_contacts()

    def _add_selected_to_numbers(_=None):
        if lst_contacts.curselection():
            num = lst_contacts.get(lst_contacts.curselection()[0])
            txt_numbers.insert(tk.END, num + "\n")
    lst_contacts.bind("<Double-1>", _add_selected_to_numbers)

    # ---------- yeni numara ekleme --------------------------------
    ent_new = tk.Entry(frm_book, width=20)
    ent_new.grid(row=1, column=1, sticky="w")

    def _save_new_number():
        num = ent_new.get().strip().lstrip("+")
        if not num.isdigit():
            toast("Geçersiz numara!", 1500); return
        cur = load_contacts()
        if num in cur:
            toast("Zaten kayıtlı.", 1500); return
        cur.append(num)
        save_contacts(cur)
        _populate_contacts()
        txt_numbers.insert(tk.END, num + "\n")
        ent_new.delete(0, tk.END)
        toast("Kaydedildi ✓", 1200)

    # ---------- seçili numarayı sil -------------------------------
    def _delete_selected():
        if not lst_contacts.curselection():
            toast("Listeden bir numara seç!", 1500); return
        num = lst_contacts.get(lst_contacts.curselection()[0])

        contacts = load_contacts()
        if num in contacts:
            contacts.remove(num)
            save_contacts(contacts)
            _populate_contacts()

        # numaralar kutusundan da çıkar
        lines = [ln for ln in txt_numbers.get("1.0", tk.END).splitlines()
                 if ln.strip() != num]
        txt_numbers.delete("1.0", tk.END)
        if lines:
            txt_numbers.insert("1.0", "\n".join(lines) + "\n")
        toast("Silindi ✓", 1200)

    # ---------- düğmeler: Kaydet & Sil ----------------------------
    tk.Button(
        frm_book, text="Save", command=_save_new_number,
        bg="#0066cc", fg="white", width=8
    ).grid(row=1, column=2, padx=5)

    tk.Button(
        frm_book, text="Delete", command=_delete_selected,
        bg="#cc0000", fg="white", width=8
    ).grid(row=1, column=3, padx=5)
    # --- numaralar ------------------------------------------------
    frm_n = tk.Frame(scroll_frame, padx=5, pady=5)
    frm_n.pack(anchor="w", fill="x")
    tk.Label(frm_n, text="Write the numbers one below the other:").pack(anchor="w")
    txt_numbers = tk.Text(frm_n, width=66, height=8)
    txt_numbers.pack(fill="x")

    # --- mesaj kutuları ------------------------------------------
    def _msg_block(parent, title):
        frm = tk.Frame(parent, padx=5, pady=5); frm.pack(anchor="w", fill="x")
        tk.Label(frm, text=title).pack(anchor="w")
        txt = tk.Text(frm, width=66, height=6); txt.pack(fill="x")
        return txt

    txt_msg1 = _msg_block(scroll_frame, "1. Message:")
    txt_msg2 = _msg_block(scroll_frame, "2. Message:")
    txt_msg3 = _msg_block(scroll_frame, "3. Message:")

    # --- mod seçimi ----------------------------------------------
    mode_var = tk.StringVar(value="instant")
    frm_mode = tk.Frame(scroll_frame, padx=5, pady=5)
    frm_mode.pack(anchor="w")
    tk.Radiobutton(frm_mode, text="Instant", variable=mode_var,
                   value="instant").grid(row=0, column=0, padx=5)
    tk.Radiobutton(frm_mode, text="Scheduled", variable=mode_var,
                   value="scheduled").grid(row=0, column=1, padx=5)

    # --- tarih spinbox'ları --------------------------------------
    frm_date = tk.Frame(scroll_frame, padx=5, pady=5)
    frm_date.pack(anchor="w")
    tk.Label(frm_date, text="Date (YYYY-MM-DD):").grid(row=0, column=0, sticky="w")
    sb_year  = tk.Spinbox(frm_date, from_=datetime.now().year,
                          to=datetime.now().year + 5, width=5, format="%04.0f")
    sb_month = tk.Spinbox(frm_date, from_=1, to=12, width=3, format="%02.0f")
    sb_day   = tk.Spinbox(frm_date, from_=1, to=31, width=3, format="%02.0f")
    for sb, val, col in [(sb_year, datetime.now().year, 1),
                         (sb_month, f"{datetime.now().month:02d}", 3),
                         (sb_day,   f"{datetime.now().day:02d}",   5)]:
        sb.delete(0, tk.END); sb.insert(0, val); sb.grid(row=0, column=col, padx=2)
    tk.Label(frm_date, text="-").grid(row=0, column=2)
    tk.Label(frm_date, text="-").grid(row=0, column=4)

    # --- saat spinbox'ları ---------------------------------------
    def _time_row(parent, default_h):
        frm = tk.Frame(parent, padx=5, pady=2); frm.pack(anchor="w")
        tk.Label(frm, text="Hour (HH:MM):").grid(row=0, column=0, sticky="w")
        sb_h = tk.Spinbox(frm, from_=0, to=23, width=3, format="%02.0f"); sb_h.insert(0, default_h)
        sb_m = tk.Spinbox(frm, from_=0, to=59, width=3, format="%02.0f"); sb_m.insert(0, "00")
        sb_h.grid(row=0, column=1, padx=(5,2)); tk.Label(frm,text=":").grid(row=0,column=2)
        sb_m.grid(row=0,column=3,padx=2)
        return sb_h, sb_m

    sb1_h, sb1_m = _time_row(scroll_frame, "09")
    sb2_h, sb2_m = _time_row(scroll_frame, "10")
    sb3_h, sb3_m = _time_row(scroll_frame, "11")

    # --- ek ayarlar ----------------------------------------------
    frm_gap = tk.Frame(scroll_frame, padx=5, pady=5)
    frm_gap.pack(anchor="w")
    tk.Label(frm_gap, text="Time between messages (1-60):")\
        .grid(row=0, column=0, sticky="w")
    sb_gap = tk.Spinbox(frm_gap, from_=1, to=60, width=3); sb_gap.insert(0,"5")
    sb_gap.grid(row=0, column=1, padx=(5,25))

    frm_set = tk.Frame(scroll_frame, padx=5, pady=5)
    frm_set.pack(anchor="w")
    tk.Label(frm_set, text="Send button timeout (sn):")\
        .grid(row=0,column=0,sticky="w")
    ent_wait = tk.Entry(frm_set,width=5); ent_wait.insert(0,"10")
    ent_wait.grid(row=0,column=1,padx=(5,25))
    tk.Label(frm_set, text="mode standby (sn):")\
        .grid(row=1,column=0,sticky="w",pady=(5,0))
    ent_gap = tk.Entry(frm_set,width=5); ent_gap.insert(0,"1")
    ent_gap.grid(row=1,column=1,padx=(5,25))

    # --- Gönder & Sıfırla ----------------------------------------
    btn_send = tk.Button(scroll_frame, text="Start sending",
                         bg="green", fg="white", width=42)
    btn_send.pack(pady=20)

    def restart_all():
        for w in (txt_numbers, txt_msg1, txt_msg2, txt_msg3):
            w.delete("1.0", tk.END)
        for sb,val in [(sb1_h,"09"),(sb1_m,"00"),(sb2_h,"10"),(sb2_m,"00"),
                       (sb3_h,"11"),(sb3_m,"00"),(sb_gap,"5")]:
            sb.delete(0, tk.END); sb.insert(0,val)
        for ent,val in [(ent_wait,"10"),(ent_gap,"1")]:
            ent.delete(0, tk.END); ent.insert(0,val)
        mode_var.set("instant")
        global _DRIVER
        if _driver_alive(_DRIVER):
            try: _DRIVER.quit()
            except: pass
        _DRIVER = None
        btn_send.config(state=tk.NORMAL)
        toast("Sıfırlandı ✓", 1500)

    tk.Button(scroll_frame, text="Restart", bg="orange",
              width=42, command=restart_all).pack(pady=(0,30))

    # ----------------------------- ana işlev ------------------------------
    def run_gui():
        nums = [n.strip().lstrip('+')
                for n in txt_numbers.get("1.0", tk.END).splitlines()
                if n.strip()]
        msg1 = txt_msg1.get("1.0", tk.END).strip()
        msg2 = txt_msg2.get("1.0", tk.END).strip()
        msg3 = txt_msg3.get("1.0", tk.END).strip()

        if not nums or not msg1:
            toast("Numara ve 1. mesaj zorunlu!", 2000); return

        wait_s = int(ent_wait.get() or 10)
        btn_send.config(state=tk.DISABLED)

        def exit_app():
            _close_driver()
            try:
                root.destroy()
            finally:
                import os
                os._exit(0)

        # ---------------------- ANINDA MOD ------------------------
        if mode_var.get() == "instant":
            gap = float(ent_gap.get() or 1)

            def _job():
                for m in (msg1, msg2, msg3):
                    if m.strip():
                        send_bulk(nums, m, wait_s, gap)
                toast("All messages have been sent ✓", 1500)
                root.after(1500, exit_app)

            threading.Thread(target=_job, daemon=True).start()
            toast("Gönderim başladı…", 1500)

        # ------------------- ZAMANLANMIŞ MOD ----------------------
        else:
            year  = int(sb_year.get()); month = int(sb_month.get()); day = int(sb_day.get())
            gap_sec = int(sb_gap.get())

            msgs: List[Tuple[str,int,int,int,int,int]] = [
                (msg1, year, month, day, int(sb1_h.get()), int(sb1_m.get()))
            ]
            if msg2:
                msgs.append((msg2, year, month, day, int(sb2_h.get()), int(sb2_m.get())))
            if msg3:
                msgs.append((msg3, year, month, day, int(sb3_h.get()), int(sb3_m.get())))

            schedule_multiple_messages(nums, msgs, gap_sec, wait_s)
            toast("Zamanlayıcılar ayarlandı…", 1500)

            def _monitor():
                if threading.active_count() > 1:
                    root.after(1000, _monitor)
                else:
                    toast("Tüm mesajlar gönderildi ✓", 1500)
                    root.after(1500, exit_app)

            _monitor()

    btn_send.config(command=run_gui)
    root.mainloop()

else:
    if not GUI_AVAILABLE:
        print(INSTALL_MSG)
    cli_mode()
