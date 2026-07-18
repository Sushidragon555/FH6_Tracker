# FH6 Tracker — Next Steps

## Verified Working
- [x] Credit OCR parsing (number, balance, change detection) — all unit tests pass
- [x] Balance region scanning (corner HUD) — reads every few seconds, logs changes
- [x] Full-screen popup scanning — detects screen changes every ~2s, runs OCR on change
- [x] Performance tier system (Quality/Balanced/Performance)
- [x] Auto-update on launch (git pull in launcher)
- [x] Check for Updates + Restart buttons in Settings
- [x] Dark theme improvements (treeview, scrollbar, progressbar)
- [x] Still Missing search/filter bar
- [x] Context menu fixes (were dead code due to duplicate definitions)
- [x] _stop_method_tracking no longer kills refresh loop
- [x] Owned cars cache used everywhere (no redundant disk reads)
- [x] Input validation on year and OCR region fields
- [x] ZIP restore path-traversal protection
- [x] OCR error logging instead of silent failure
- [x] OCR checkbox takes effect immediately (no need to click Save)

## Recommended But Not Done

### 1. Better popup text matching
The `_scan_fullscreen_popups` method looks for keywords like "earned", "won", "reward", etc. Some popups might use different phrasing. If the user reports misses, expand the keyword list in `fh6_gui.py:_scan_fullscreen_popups`.

### 2. Force-scan hotkey (F5)
Add a keyboard shortcut (e.g. F5) that immediately triggers a full-screen popup scan. This would let the user test detection on demand when a popup is visible.
- Bind in `__init__`: `self.bind("<F5>", lambda e: self._scan_fullscreen_popups(time.monotonic(), force=True))`
- Modify `_scan_fullscreen_popups` to accept a `force` parameter that skips the rate-limit checks

### 3. Auto-calibration still requires Forza
The auto-calibrate needs the game on screen (to find the credit number). This can't be avoided, but the manual Capture Area tool works as a fallback.

### 4. Performance tuning
On low-end PCs, the full-screen OCR might cause stuttering. If reported:
- Increase the change-detection interval (currently 2s)
- Reduce the full-scan resolution (currently 800px wide)
- Or skip full-screen scanning entirely and rely on just the balance region

### 5. OCR region presets
Save multiple credit region presets for different resolutions or games (FH4 vs FH5 vs FH6). Currently only one region is stored.

### 6. Transaction undo
No way to undo a mis-detected credit transaction. Could add an "Undo last transaction" button.

### 7. Deduplicate helpers
`normalize_car_name` and `load_json_file` are defined in both `fh6_gui.py` and `car_lookup.py`. Should import from `car_lookup.py` and remove the duplicates from `fh6_gui.py`.
