"""Credit detection test simulator.

Opens a window titled "Forza Horizon 6 - Credit Simulator" that mimics
Forza's credit HUD.  Point the tracker's OCR region at this window to
test the full OCR pipeline without running the actual game.

Usage:
    python credit_simulator.py

Then in FH6 Tracker → Settings → Automatic Credit Tracking (OCR):
  1. Enable OCR
  2. Click "Capture Area" and drag over the CREDITS number in this window
  3. Click "Auto-Detect Region" (it will find the text automatically)
  4. Go back to the Live Data tab and watch session credits increase
"""

import tkinter as tk
import random


def main():
    root = tk.Tk()
    root.title("Forza Horizon 6 - Credit Simulator")
    root.geometry("460x280")
    root.attributes("-topmost", True)
    root.configure(bg="#0d1117")

    balance = 1_050_000

    balance_var = tk.StringVar(value=f"CREDITS: {balance:,}")

    tk.Label(
        root,
        textvariable=balance_var,
        font=("Consolas", 26, "bold"),
        fg="#00ff41",
        bg="#0d1117",
    ).pack(pady=(30, 10))

    popup_var = tk.StringVar(value="")
    popup_label = tk.Label(
        root,
        textvariable=popup_var,
        font=("Segoe UI", 14),
        fg="#ffd700",
        bg="#0d1117",
        wraplength=420,
    )
    popup_label.pack(pady=(0, 20))

    def earn():
        nonlocal balance
        gain = random.choice([25000, 50000, 75000, 100000, 125000, 150000, 200000])
        balance += gain
        balance_var.set(f"CREDITS: {balance:,}")
        amounts = {
            25000: "25,000",
            50000: "50,000",
            75000: "75,000",
            100000: "100,000",
            125000: "125,000",
            150000: "150,000",
            200000: "200,000",
        }
        popup_var.set(f"You earned {amounts[gain]} credits")
        root.after(4000, lambda: popup_var.set(""))

    def spend():
        nonlocal balance
        amount = random.choice([25000, 50000, 100000, 200000])
        amount = min(amount, balance - 1000)
        if amount > 0:
            balance -= amount
            balance_var.set(f"CREDITS: {balance:,}")
            popup_var.set(f"You spent {amount:,} credits")
            root.after(4000, lambda: popup_var.set(""))

    btn_frame = tk.Frame(root, bg="#0d1117")
    btn_frame.pack(pady=10)

    tk.Button(
        btn_frame,
        text="Earn Credits",
        font=("Segoe UI", 12),
        bg="#137333",
        fg="white",
        activebackground="#1a9e4a",
        command=earn,
        width=14,
    ).pack(side=tk.LEFT, padx=6)

    tk.Button(
        btn_frame,
        text="Spend Credits",
        font=("Segoe UI", 12),
        bg="#a50e0e",
        fg="white",
        activebackground="#d11414",
        command=spend,
        width=14,
    ).pack(side=tk.LEFT, padx=6)

    tk.Label(
        root,
        text="Place OCR region over the CREDITS number above.\nClick Earn/Spend — the tracker will auto-detect changes.",
        font=("Segoe UI", 9),
        fg="#888",
        bg="#0d1117",
        justify="center",
    ).pack(side=tk.BOTTOM, pady=(0, 15))

    root.mainloop()


if __name__ == "__main__":
    main()
