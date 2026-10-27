"""ORDERSYS - legacy green-screen order entry (curses).

Business rules live inside the screen handlers on purpose: this is the
realistic mess a migration has to untangle.

Keys: F3=Exit  F5=Refresh  F12=Cancel  ENTER=Select/Submit
"""
import curses
import os

import db

os.environ.setdefault("ESCDELAY", "25")   # make ESC respond immediately
BACK_KEYS = (curses.KEY_F12, 27)          # F12 or ESC (Macs often eat F-keys)

GREEN = 1
TAX_RATE = 0.0825


def put(scr, y, x, text, attr=0):
    try:
        scr.addstr(y, x, text, attr)
    except curses.error:
        pass


def frame(scr, title, footer):
    scr.erase()
    h, w = scr.getmaxyx()
    put(scr, 0, 0, (" ORDERSYS  " + title).ljust(w - 1), curses.A_REVERSE)
    put(scr, h - 1, 0, footer.ljust(w - 1), curses.A_REVERSE)


def prompt(scr, y, x, label, width):
    """Read a field. Returns None if F12 pressed, 'EXIT' on F3."""
    put(scr, y, x, label)
    fx = x + len(label)
    buf = ""
    curses.curs_set(1)
    while True:
        put(scr, y, fx, buf.ljust(width), curses.A_UNDERLINE)
        scr.move(y, fx + len(buf))
        ch = scr.getch()
        if ch in (10, 13, curses.KEY_ENTER):
            return buf
        if ch in BACK_KEYS:
            return None
        if ch == curses.KEY_F3:
            return "EXIT"
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif 32 <= ch < 127 and len(buf) < width:
            buf += chr(ch).upper()


def message(scr, y, text):
    put(scr, y, 2, text.ljust(70), curses.A_BOLD)
    scr.refresh()
    scr.getch()


def menu(scr):
    while True:
        frame(scr, "MAIN MENU", "F3/Q=Exit   (ESC = back on any screen)")
        put(scr, 3, 4, "1. Customer lookup")
        put(scr, 4, 4, "2. Order entry")
        put(scr, 5, 4, "3. Order list")
        put(scr, 6, 4, "4. Sales report")
        put(scr, 7, 4, "5. Customer name search")
        put(scr, 9, 4, "Selection: ")
        curses.curs_set(1)
        ch = scr.getch()
        if ch == curses.KEY_F3 or ch in (ord("q"), ord("Q")):
            return
        if ch == ord("1"):
            customer_screen(scr)
        elif ch == ord("2"):
            order_entry_screen(scr)
        elif ch == ord("3"):
            order_list_screen(scr)
        elif ch == ord("4"):
            report_screen(scr)
        elif ch == ord("5"):
            search_screen(scr)


def customer_screen(scr):
    conn = db.connect()
    while True:
        frame(scr, "CUSTOMER LOOKUP", "ENTER=Search  F12=Back  F3=Exit")
        v = prompt(scr, 3, 2, "Customer no: ", 8)
        if v is None:
            return
        if v == "EXIT":
            raise SystemExit
        row = conn.execute(
            "SELECT cust_no,name,city,tier,credit_lim FROM customers WHERE cust_no=?",
            (v,),
        ).fetchone()
        if not row:
            message(scr, 6, "CUSTOMER NOT FOUND - PRESS ANY KEY")
            continue
        tier = {"S": "STANDARD", "G": "GOLD", "P": "PLATINUM"}[row[3]]
        used = conn.execute(
            "SELECT COALESCE(SUM(total_cents),0) FROM orders "
            "WHERE cust_no=? AND status IN ('N','P')", (row[0],)
        ).fetchone()[0]
        put(scr, 6, 2, "Name   : %s" % row[1])
        put(scr, 7, 2, "City   : %s" % row[2])
        put(scr, 8, 2, "Tier   : %s" % tier)
        put(scr, 9, 2, "Credit : $%d" % row[4])
        put(scr, 10, 2, "Open   : $%.2f" % (used / 100.0))
        message(scr, 12, "PRESS ANY KEY")


def search_screen(scr):
    conn = db.connect()
    frame(scr, "CUSTOMER NAME SEARCH", "ENTER=Search  ESC=Back")
    term = prompt(scr, 3, 2, "Name contains: ", 20)
    if not term or term == "EXIT":
        return
    sql = "SELECT cust_no,name,city FROM customers WHERE name LIKE '%" + term + "%'"
    rows = conn.execute(sql).fetchall()
    for i, r in enumerate(rows[:12]):
        put(scr, 5 + i, 2, "%-7s %-22s %s" % r)
    if not rows:
        put(scr, 5, 2, "NO MATCHES")
    message(scr, 18, "PRESS ANY KEY")


def order_entry_screen(scr):
    conn = db.connect()
    frame(scr, "ORDER ENTRY", "ENTER=Next  F12=Cancel  F3=Exit")
    cust = prompt(scr, 3, 2, "Customer no : ", 8)
    if cust is None:
        return
    if cust == "EXIT":
        raise SystemExit
    c = conn.execute("SELECT tier,credit_lim,name FROM customers WHERE cust_no=?",
                     (cust,)).fetchone()
    if not c:
        message(scr, 12, "INVALID CUSTOMER - PRESS ANY KEY")
        return
    put(scr, 3, 40, c[2])
    sku = prompt(scr, 5, 2, "SKU         : ", 8)
    if sku is None:
        return
    if sku == "EXIT":
        raise SystemExit
    p = conn.execute("SELECT descr,price_cents,stock FROM products WHERE sku=?",
                     (sku,)).fetchone()
    if not p:
        message(scr, 12, "INVALID SKU - PRESS ANY KEY")
        return
    put(scr, 5, 40, "%s  $%.2f  STK %d" % (p[0], p[1] / 100.0, p[2]))
    q = prompt(scr, 7, 2, "Quantity    : ", 4)
    if q is None:
        return
    if q == "EXIT":
        raise SystemExit
    if not q.isdigit() or int(q) < 1:
        message(scr, 12, "QTY MUST BE A POSITIVE NUMBER - PRESS ANY KEY")
        return
    qty = int(q)
    if qty > p[2]:
        message(scr, 12, "INSUFFICIENT STOCK - PRESS ANY KEY")
        return

    # --- pricing rules ---
    gross = p[1] * qty
    disc_pct = 0
    if c[0] == "G":
        disc_pct = 5
    elif c[0] == "P":
        disc_pct = 10
    if qty >= 100:               # volume break stacks with tier
        disc_pct += 3
    # QUIRK: discount truncates (int) instead of rounding
    discount = int(gross * disc_pct / 100)
    net = gross - discount
    # QUIRK: tax rounds half DOWN to the cent
    tax = int(net * TAX_RATE)
    total = net + tax

    # --- credit check ---
    used = conn.execute(
        "SELECT COALESCE(SUM(total_cents),0) FROM orders "
        "WHERE cust_no=? AND status IN ('N','P')", (cust,)
    ).fetchone()[0]
    if used + total > c[1] * 100:
        message(scr, 12, "CREDIT LIMIT EXCEEDED - ORDER REJECTED")
        return

    put(scr, 9, 2, "Gross   : $%.2f" % (gross / 100.0))
    put(scr, 10, 2, "Discount: $%.2f (%d%%)" % (discount / 100.0, disc_pct))
    put(scr, 11, 2, "Tax     : $%.2f" % (tax / 100.0))
    put(scr, 12, 2, "TOTAL   : $%.2f" % (total / 100.0))
    ok = prompt(scr, 14, 2, "Confirm (Y/N): ", 1)
    if ok != "Y":
        return
    cur = conn.execute(
        "INSERT INTO orders (cust_no,sku,qty,total_cents,status) VALUES (?,?,?,?,'N')",
        (cust, sku, qty, total))
    conn.execute("UPDATE products SET stock=stock-? WHERE sku=?", (qty, sku))
    conn.commit()
    message(scr, 16, "ORDER %d CREATED - PRESS ANY KEY" % cur.lastrowid)


def order_list_screen(scr):
    conn = db.connect()
    top, sel = 0, 0
    while True:
        rows = conn.execute(
            "SELECT order_no,cust_no,sku,qty,total_cents,status FROM orders "
            "ORDER BY order_no DESC").fetchall()
        frame(scr, "ORDER LIST",
              "F3=Exit F12/ESC/B=Back  P=Pick S=Ship C=Cancel")
        put(scr, 2, 2, "ORDER  CUST    SKU     QTY     TOTAL  ST")
        h, _ = scr.getmaxyx()
        page = h - 6
        if not rows:
            put(scr, 4, 2, "NO ORDERS ON FILE - CREATE ONE VIA ORDER ENTRY")
        for i, r in enumerate(rows[top:top + page]):
            line = "%-6d %-7s %-7s %3d %9.2f  %s" % (
                r[0], r[1], r[2], r[3], r[4] / 100.0, r[5])
            put(scr, 3 + i, 2, line,
                curses.A_REVERSE if top + i == sel else 0)
        ch = scr.getch()
        if ch in BACK_KEYS or ch in (ord("b"), ord("B")):
            return
        if ch == curses.KEY_F3:
            raise SystemExit
        if ch == curses.KEY_DOWN and sel < len(rows) - 1:
            sel += 1
            if sel >= top + page:
                top += 1
        elif ch == curses.KEY_UP and sel > 0:
            sel -= 1
            if sel < top:
                top -= 1
        elif ch in (ord("p"), ord("P"), ord("s"), ord("S"), ord("c"), ord("C")) and rows:
            o = rows[sel]
            cur_status = o[5]
            k = chr(ch).upper()
            # status transitions: N->P->S ; C allowed from N or P only
            if k == "P" and cur_status == "N":
                conn.execute("UPDATE orders SET status='P' WHERE order_no=?", (o[0],))
            elif k == "S" and cur_status == "P":
                conn.execute("UPDATE orders SET status='S' WHERE order_no=?", (o[0],))
            elif k == "C" and cur_status in ("N", "P"):
                conn.execute("UPDATE orders SET status='C' WHERE order_no=?", (o[0],))
                conn.execute("UPDATE products SET stock=stock+? WHERE sku=?", (o[3], o[2]))
            conn.commit()


def report_screen(scr):
    conn = db.connect()
    frame(scr, "SALES REPORT", "F12/ESC/B=Back")
    rows = conn.execute(
        "SELECT c.name, COUNT(o.order_no), COALESCE(SUM(o.total_cents),0) "
        "FROM customers c LEFT JOIN orders o "
        "ON o.cust_no=c.cust_no AND o.status<>'C' "
        "GROUP BY c.cust_no ORDER BY 3 DESC").fetchall()
    put(scr, 2, 2, "CUSTOMER                 ORDERS      SALES")
    grand = 0
    for i, r in enumerate(rows):
        put(scr, 3 + i, 2, "%-22s %6d %10.2f" % (r[0], r[1], r[2] / 100.0))
        grand += r[2]
    put(scr, 4 + len(rows), 2, "%-22s %6s %10.2f" % ("GRAND TOTAL", "", grand / 100.0))
    scr.refresh()
    while True:
        ch = scr.getch()
        if ch in BACK_KEYS or ch in (ord("b"), ord("B")):
            return


def main(scr):
    curses.start_color()
    curses.init_pair(GREEN, curses.COLOR_GREEN, curses.COLOR_BLACK)
    scr.bkgd(" ", curses.color_pair(GREEN))
    scr.keypad(True)
    menu(scr)


if __name__ == "__main__":
    db.init()
    try:
        curses.wrapper(main)
    except SystemExit:
        pass
