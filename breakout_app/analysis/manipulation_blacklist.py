# -*- coding: utf-8 -*-
"""Danh sách mã đã bị KẾT LUẬN thao túng trên TTCK Việt Nam (backtest hygiene).

Nguyên tắc dùng (chống look-ahead bias — xem DEVELOPMENT #34):
  - KHÔNG xóa khỏi dữ liệu backtest — chỉ GẮN THẺ. Kết quả "chính" loại các dòng
    (mã, ngày) rơi vào cửa sổ thao túng; nhưng luôn báo cáo kèm phần đóng góp của
    nhóm thao túng, vì screener chạy live KHÔNG biết trước vụ án nào chưa bị phanh
    phui — tương lai sẽ còn những "FLC mới", và CV cap/thanh khoản của spec phải
    được kiểm chứng xem có tự phòng vệ được không.
  - ``window = None`` → loại toàn bộ vòng đời (công ty vỏ, cả đời gắn với hệ sinh
    thái thao túng). Có window → chỉ loại giai đoạn bơm-xả theo cáo trạng/kết luận.

Nguồn: bản án/cáo trạng công khai (FLC 7/2024, Louis 5/2023, MTM 5/2020…) và kết
luận UBCKNN. Danh sách CÓ THỂ BỔ SUNG — thêm mã mới vào dict là báo cáo tự cập nhật.
"""

# symbol → {group, case, window: (from, to) | None}
BLACKLIST = {
    # ── Nhóm FLC — vụ Trịnh Văn Quyết (tuyên án 7/2024) ────────────────────────────
    "FLC": {"group": "FLC", "case": "Trịnh Văn Quyết — thao túng 2017-2022", "window": ("2016-01-01", "2022-12-31")},
    "ROS": {"group": "FLC", "case": "FLC Faros — vốn khống + bơm xả", "window": None},
    "AMD": {"group": "FLC", "case": "FLC Stone", "window": None},
    "KLF": {"group": "FLC", "case": "FLC GAB Invest", "window": None},
    "ART": {"group": "FLC", "case": "Chứng khoán BOS", "window": None},
    "HAI": {"group": "FLC", "case": "Nông dược HAI", "window": ("2016-01-01", "2022-12-31")},
    "GAB": {"group": "FLC", "case": "FLC GAB", "window": None},

    # ── Nhóm Louis Holdings — vụ Đỗ Thành Nhân (tuyên án 5/2023) ───────────────────
    "TGG": {"group": "Louis", "case": "Louis Capital — thao túng 1-10/2021", "window": None},
    "BII": {"group": "Louis", "case": "Louis Land — thao túng 1-10/2021", "window": None},
    "APG": {"group": "Louis", "case": "CK APG (hệ sinh thái Louis)", "window": ("2021-01-01", "2022-06-30")},
    "AGM": {"group": "Louis", "case": "Angimex (Louis thâu tóm)", "window": ("2021-01-01", "2022-12-31")},
    "LDP": {"group": "Louis", "case": "Dược Lâm Đồng (Louis)", "window": ("2021-01-01", "2022-06-30")},
    "DDV": {"group": "Louis", "case": "DAP Vinachem (sóng Louis)", "window": ("2021-01-01", "2022-06-30")},
    "SMT": {"group": "Louis", "case": "Sametel (Louis)", "window": ("2021-01-01", "2022-06-30")},

    # ── Nhóm APEC — vụ Nguyễn Đỗ Lăng (khởi tố 6/2023, xét xử 2024) ────────────────
    "API": {"group": "APEC", "case": "Đầu tư Châu Á TBD — bơm ×10 năm 2021", "window": ("2021-01-01", "2022-12-31")},
    "APS": {"group": "APEC", "case": "Chứng khoán APEC", "window": ("2021-01-01", "2022-12-31")},
    "IDJ": {"group": "APEC", "case": "IDJ Việt Nam", "window": ("2021-01-01", "2022-12-31")},

    # ── Trí Việt — vụ TVB/TVC (Đỗ Đức Nam/Phạm Thanh Tùng, gắn vụ Louis) ───────────
    "TVB": {"group": "TriViet", "case": "CK Trí Việt", "window": ("2020-12-01", "2022-06-30")},
    "TVC": {"group": "TriViet", "case": "Tập đoàn Trí Việt", "window": ("2020-12-01", "2022-06-30")},

    # ── Các vụ án lẻ đã tuyên (trong cửa sổ dữ liệu 2014→) ─────────────────────────
    "KSA": {"group": "Khác", "case": "Khoáng sản Bình Thuận — Phạm Thị Hinh (án 2020)", "window": ("2015-09-01", "2016-12-31")},
    "CDO": {"group": "Khác", "case": "Tư vấn ĐT&PT Đô thị — Nguyễn Vân Giang (án 2018)", "window": ("2015-01-01", "2017-06-30")},
    "KVC": {"group": "Khác", "case": "Inox Kim Vĩ (án 2019)", "window": ("2015-11-01", "2017-06-30")},
    "MTM": {"group": "Khác", "case": "Mỏ & KS Miền Trung (án 2020, UPCOM)", "window": None},
    "FTM": {"group": "Khác", "case": "Đức Quân Fortex — kết luận UBCK (hành chính)", "window": ("2019-01-01", "2019-12-31")},
}


def tag(df, date_col: str = "date", symbol_col: str = "symbol"):
    """Thêm cột bool ``manip`` = dòng rơi vào cửa sổ thao túng đã kết luận."""
    import pandas as pd
    mask = pd.Series(False, index=df.index)
    for sym, info in BLACKLIST.items():
        m = df[symbol_col] == sym
        if not m.any():
            continue
        w = info["window"]
        if w is None:
            mask |= m
        else:
            mask |= m & (df[date_col] >= w[0]) & (df[date_col] <= w[1])
    out = df.copy()
    out["manip"] = mask
    return out
