# Sửa lỗi đo lợi suất qua ngày GDKHQ cổ tức tiền mặt

> Tài liệu bàn giao cho session làm việc trên `breakout_app`. Tự chứa — không cần
> ngữ cảnh từ nơi phát hiện. **Hãy tự chạy lại các phép kiểm ở §2 trước khi tin.**
>
> ✅ **ĐÃ TRIỂN KHAI 03/08/2026** — §2 tự kiểm chứng lại (khớp), §4 code đủ 4 bước
> (+ chốt đơn vị 30%), §6a backfill toàn bộ signal_outcomes + daily_observations
> (6 tín hiệu & 38 quan sát được sửa; MBB 07/07 −0.44→**+3.59**, 08/07 −2.20→**+1.80**
> — chênh so với ước lượng +3.44/+1.68 trong §2.3 là do §2.3 dùng suất 3.88% trên mốc
> giá khác; số mới tự nhất quán với mẫu số của ret). §6b (kho backtest 10 năm) **chủ
> đích không làm** theo khuyến nghị §2.4. §5 pass 5/5. §7 đã đính chính hồ sơ P11 +
> loss_reviews. Chi tiết: DEVELOPMENT.md #45.

---

## 1. Vấn đề một câu

`signal_outcomes.ret_t1..t5`, `mfe`, `mae`, `win_t3` (và bản song sinh của chúng
trong `daily_observations`) đang ghi nhận **một khoản lỗ không có thật** cho mọi
lệnh đi qua ngày giao dịch không hưởng quyền (GDKHQ) cổ tức **tiền mặt**, đúng
bằng suất cổ tức.

Nguyên nhân: nguồn dữ liệu VCI **hồi tố** giá cho chia tách / cổ tức cổ phiếu,
nhưng **KHÔNG hồi tố** cổ tức tiền mặt. Vào ngày GDKHQ, giá tham chiếu bị trừ
đúng bằng cổ tức, nhưng người nắm giữ **vẫn nhận được tiền** — nên lợi suất
close-to-close tính trên giá thô hụt đi đúng phần tiền đó.

### Đây KHÔNG phải lỗi mà bản vá PET đã xử lý

`data/db.py::close_on` (bản vá sau sự cố PET −31% ngày 09/07/2026) giải quyết
**lệch hệ quy chiếu**: giá vào lệnh chưa điều chỉnh so với giá ra đã điều chỉnh,
sinh ra lỗ ảo khổng lồ. Bản vá đó đúng và vẫn cần.

Ca cổ tức tiền mặt là **cơ chế khác**: cả hai đầu đều trên cùng một hệ (đều thô),
`close_on` không phát hiện được gì bất thường. Vấn đề là **giá trị cổ tức đã thực
sự rời khỏi giá** và không có chỗ nào cộng nó trở lại.

---

## 2. Bằng chứng — hãy tự chạy lại

### 2.1 Kiểm nguồn dữ liệu có hồi tố cổ tức tiền mặt không

VHM chi cổ tức tiền mặt 6.000đ/cp, GDKHQ **29/06/2026**. Nếu VCI hồi tố thì giá
lịch sử trước ngày đó phải bị hạ xuống khi lấy lại hôm nay.

```python
from vnstock import Quote
d = Quote(symbol='VHM', source='VCI').history(
        start='2026-06-24', end='2026-06-30', interval='1D')
print(d[['time','close']])
```

So với `ohlcv_daily` đã lưu từ hồi tháng 6:

```sql
SELECT date, close FROM ohlcv_daily
WHERE symbol='VHM' AND date BETWEEN '2026-06-24' AND '2026-06-30';
```

**Kết quả quan sát được:** khớp từng đồng (lệch ≤ 0,01%). 26/06 vẫn là 156.000 ở
cả hai nơi → **không có hồi tố nào**.

### 2.2 Kiểm rằng chia tách THÌ ĐƯỢC hồi tố (để biết phạm vi sửa)

VHM trả cổ tức **cổ phiếu** 30%, GDKHQ 15/09/2021. Nếu không hồi tố thì phải
thấy bậc nhảy ~−23% (= 1 − 1/1,3).

```python
Quote(symbol='VHM', source='VCI').history(start='2021-09-10', end='2021-09-20', interval='1D')
```

**Kết quả:** chuỗi mượt, không có bậc nhảy. → Chia tách và cổ tức cổ phiếu **đã
được hồi tố**, không cần sửa. **Chỉ cổ tức tiền mặt mới cần.**

### 2.3 Đo mức độ ảnh hưởng trên chính DB sản xuất

Đối chiếu `signal_outcomes` với lịch `Company(sym).events()` lọc `event_code=='DIV'`,
tìm các lệnh có `exright_date` rơi trong 8 ngày lịch sau `reco_date`:

| Mã | Ngày KN | Cổ tức | Suất | `ret_t3` ghi nhận | Thực tế | `win_t3` |
|---|---|---:|---:|---:|---:|---|
| MBB | 2026-07-06 | 1.000đ | 3,88% | +1,11 | **+4,99** | 1 |
| MBB | 2026-07-07 | 1.000đ | 3,88% | −0,44 | **+3,44** | **0 → sai** |
| MBB | 2026-07-08 | 1.000đ | 3,88% | −2,20 | **+1,68** | **0 → sai** |
| VCG | 2026-07-09 | 800đ | 3,77% | −4,56 | −0,78 | 0 |

- **4/86 lệnh (4,7%)** dính.
- Méo trung bình trên toàn bộ mẫu: **+0,18 điểm phần trăm/lệnh**.
- **2/86 lệnh (2,3%)** bị xếp nhầm **thắng thành thua**.

### 2.4 KHÔNG có méo chọn mẫu — điểm quan trọng nhất

Giả thuyết đáng lo: tiền săn cổ tức tạo cú tăng giá + volume trông y hệt breakout,
nên khuyến nghị có thể **dồn cụm** quanh ngày GDKHQ. Kiểm bằng cách so với chính
pool trong cùng kỳ:

| | Tỷ lệ có GDKHQ tiền mặt trong 8 ngày tới |
|---|---|
| Pool nền (575 mã-ngày) | **4,70%** |
| Lệnh được khuyến nghị (4/86) | **4,65%** |

Bằng nhau → **không dồn cụm**. Đây là **méo đo lường đều tay**, không phải méo
chọn mẫu.

**Hệ quả rất quan trọng:** méo này trừ cả lệnh khuyến nghị lẫn baseline pool như
nhau, nên nó **triệt tiêu trong hiệu số**. Lợi thế đo được (+0,29%/lệnh ở TRAIN)
**gần như không đổi sau khi sửa**. Đừng kỳ vọng sửa xong thì lợi thế đẹp lên.

---

## 3. Sửa thì được gì — cân nhắc trước khi làm

**Không được gì nếu** mục tiêu duy nhất là đo lợi thế của công thức (§2.4).

**Được ba thứ này:**

| | Đang sai thế nào |
|---|---|
| Cột T+1..T+5 trên dashboard | Hiện lỗ không có thật |
| Tỷ lệ thắng | ~2,3% số lệnh xếp nhầm |
| Sàng lọc `loss_reviews` | Lệnh **có lãi** bị đưa vào diện điều tra thua lỗ |

Cột thứ ba là lý do mạnh nhất — nó tiêu tốn thời gian người thật. Ca cụ thể:
MBB 07/07 và 08/07 đều nằm trong `loss_reviews`, quy nguyên nhân cho
`thị_trường_đỏ`, ghi chú *"gần hoà vốn −0.44%"*. Con số thật là **+3,44%**. Tức
là đã rà nguyên nhân cho một thất bại không tồn tại, và rút ra kết luận sai về
một hiện tượng không xảy ra.

---

## 4. Cách sửa

### Vật cản: `forward_closes` không trả về ngày

```python
# data/db.py:291
def forward_closes(symbol: str, reco_date: str, n: int = 5):
    q = ("SELECT close FROM ohlcv_daily WHERE symbol=? AND date>? ORDER BY date LIMIT ?")
```

Không có ngày thì không biết phiên nào đã đi qua GDKHQ. Nên phải sửa 4 chỗ.

### Bước 1 — bảng lịch cổ tức

Thêm vào schema (`data/db.py`, cạnh các `CREATE TABLE` khác, ~dòng 66):

```sql
CREATE TABLE IF NOT EXISTS cash_dividends (
  symbol          TEXT NOT NULL,
  exright_date    TEXT NOT NULL,          -- YYYY-MM-DD
  value_per_share REAL NOT NULL,          -- ĐỒNG/cp
  PRIMARY KEY (symbol, exright_date)
);
```

### Bước 2 — nạp lịch, MỘT LẦN/NGÀY

`Company(sym).events()` trả **toàn bộ lịch sử** nên một lần gọi là đủ cho cả quá
khứ lẫn tương lai. Chạy cùng lượt fetch universe hằng ngày, **không** chạy mỗi
scan 5 phút.

```python
def refresh_dividend_calendar(symbols):
    """Nạp lịch cổ tức tiền mặt. events() trả cả lịch sử nên gọi 1 lần/ngày là đủ."""
    from vnstock import Company
    import pandas as pd
    rows = []
    for s in symbols:
        try:
            d = Company(symbol=s, source="VCI").events()
        except Exception:
            continue                      # bỏ qua mã lỗi, lần sau nạp lại
        for _, r in d[d.event_code == "DIV"].iterrows():
            if pd.isna(r["exright_date"]) or pd.isna(r["value_per_share"]):
                continue
            rows.append((s, pd.to_datetime(r["exright_date"]).strftime("%Y-%m-%d"),
                         float(r["value_per_share"])))
    with _conn() as c:
        c.executemany(
            "INSERT INTO cash_dividends(symbol,exright_date,value_per_share) "
            "VALUES (?,?,?) ON CONFLICT(symbol,exright_date) DO UPDATE SET "
            "value_per_share=excluded.value_per_share", rows)
    return len(rows)
```

> **Về đơn vị — đã kiểm, app AN TOÀN.** `ohlcv_daily` lưu theo **đồng**
> (MBB = 23.800) và `events().value_per_share` cũng theo **đồng** (1.000). Cùng
> đơn vị, cộng thẳng được.
>
> Cạm bẫy: `Quote.history()` của VCI trả giá theo **nghìn đồng** (MBB = 23,8).
> Nếu chỗ nào cộng cổ tức vào chuỗi giá LẤY TRỰC TIẾP từ `Quote` mà chưa nhân
> 1000 thì sẽ ra lợi suất kiểu **+4.029%**. Nên thêm chốt kiểm: suất cổ tức tính
> ra phải ≤ 30% thị giá, vượt thì **từ chối hiệu chỉnh** kèm cảnh báo, đừng lặng
> lẽ cho ra số rác.

### Bước 3 — `forward_closes` trả kèm ngày

```python
# data/db.py:291
def forward_closes(symbol: str, reco_date: str, n: int = 5):
    """Tối đa n phiên SAU reco_date, tăng dần. Trả [(date, close)] — cần date để
    biết phiên nào đã đi qua GDKHQ cổ tức tiền mặt."""
    q = ("SELECT date, close FROM ohlcv_daily WHERE symbol=? AND date>? "
         "ORDER BY date LIMIT ?")
    with _conn() as c:
        return c.execute(q, (symbol, reco_date, n)).fetchall()
```

⚠️ **Đổi kiểu trả về** → phải sửa hai chỗ gọi: `scheduler.py:338` và
`scheduler.py:356`. Hai chỗ này chỉ chuyển tiếp `closes` xuống hàm dưới nên sửa
nhẹ, nhưng đừng bỏ sót.

### Bước 4 — hàm tra cứu + phép tính mới

```python
def dividends_between(symbol: str, after_date: str, upto_date: str) -> float:
    """Cổ tức tiền mặt/cp nhận được nếu vào lệnh ngày `after_date`, giữ tới `upto_date`.

    Luật Việt Nam: mua ở phiên CUỐI trước ngày GDKHQ là còn hưởng quyền — nên
    điều kiện là exright_date > after_date (chặt) và <= upto_date.
    Kiểm chứng: VHM GDKHQ 29/06/2026 (thứ Hai), 26/06 (thứ Sáu) là phiên cuối
    còn hưởng quyền.
    """
    q = ("SELECT COALESCE(SUM(value_per_share),0) FROM cash_dividends "
         "WHERE symbol=? AND exright_date>? AND exright_date<=?")
    with _conn() as c:
        return c.execute(q, (symbol, after_date, upto_date)).fetchone()[0]
```

**`data/db.py:317`** trong `upsert_outcome`:

```python
# CŨ:
rets = [(c / reco_close - 1) * 100 for c in closes]

# MỚI — closes giờ là [(date, close)]:
rets = [((c + dividends_between(symbol, reco_date, d)) / reco_close - 1) * 100
        for d, c in closes]
```

**`data/db.py:522`** trong `update_observation_outcome` — y hệt:

```python
rets = [((c + dividends_between(symbol, obs_date, d)) / close_ref - 1) * 100
        for d, c in closes]
```

`cat(i)` (lưu `close_t1..t5`) nên giữ **giá thô** — đó là giá thật đã khớp trên
sàn. Chỉ `ret_*` mới hiệu chỉnh. `mfe`/`mae`/`win_t3` là hàm của `rets` nên tự
đúng theo.

---

## 5. Kiểm chứng sau khi sửa

1. **Ca hồi quy có sẵn.** MBB `reco_date = 2026-07-07`: `ret_t3` phải đi từ
   **−0,44 → +3,44** và `win_t3` từ **0 → 1**. Tương tự 08/07: **−2,20 → +1,68**.
2. **Không đổi ở lệnh sạch.** Lấy một lệnh không đi qua GDKHQ nào — mọi `ret_*`
   phải **giống hệt** trước khi sửa. Nếu đổi thì điều kiện ngày đang sai.
3. **Biên hưởng quyền.** Lệnh vào **đúng ngày GDKHQ** phải nhận **0đ** cổ tức
   (mua ngày GDKHQ là không còn quyền). Lệnh vào phiên liền trước phải nhận đủ.
4. **Chốt đơn vị.** Không lệnh nào được có phần cổ tức > 30% giá vào lệnh.
5. **Kiểm âm.** Tắt tạm `dividends_between` (trả 0) → kết quả phải quay về đúng
   số cũ. Nếu không thì có chỗ khác cũng bị đổi ngoài ý muốn.

---

## 6. Hai việc phải quyết trước khi ship

**a) Nạp lại dữ liệu cũ?** Sau khi sửa, `ret_*` mới và cũ **không so được với
nhau**. Lịch cổ tức tra ngược được nên nạp lại toàn bộ `signal_outcomes` và
`daily_observations` là khả thi — chạy lại `_update_outcomes()` /
`_update_observation_outcomes()` bỏ điều kiện `cutoff`. Nếu **không** nạp lại thì
phải ghi rõ mốc thời gian đổi hệ đo, nếu không mọi so sánh xuyên mốc đều sai.

**b) Kho backtest 10 năm?** Cũng dính lỗi này. Nhưng theo §2.4 **lợi thế đo được
gần như không đổi** (méo triệt tiêu giữa khuyến nghị và pool), nên trừ khi cần
con số lợi suất **tuyệt đối** đúng, việc này ưu tiên thấp. Nếu làm thì cần lịch
cổ tức lịch sử cho toàn bộ vũ trụ mã — tốn hơn nhiều so với phần live.

---

## 7. Ghi chú liên quan: điểm mù P11 vẫn còn nguyên

Tài liệu này **chỉ sửa cách ĐO**, không sửa **điểm mù nhận diện**.

Pattern P11 `sự_kiện_cổ_tức` (ca VHM 26/06) là chuyện khác: hệ chỉ đọc OHLCV
không phân biệt được **tiền breakout** với **tiền săn cổ tức** — hai loại để lại
dấu vết giống hệt nhau (giá tăng, volume đột biến) nhưng động cơ ngược nhau. Fix
đề xuất (cảnh báo khi ngày khuyến nghị sát GDKHQ) vẫn đang chờ đủ 3 ca theo luật
parsimony. Sửa §4 **không** thay thế được việc đó.

Một chi tiết nên đính chính trong hồ sơ P11: ghi chú hiện viết *"29/6 (GDKHQ)
người săn cổ tức thoát → −3,65%"*. Số liệu phiên đó:

```
26/06  close 156.000                                    ← cổ tức 6.000đ
29/06  open 158.000  high 158.100  low 150.100  close 150.300
```

Giá tham chiếu điều chỉnh cho 29/06 là **150.000**; close thực tế **150.300**,
tức **đóng cửa ngay tại tham chiếu đã điều chỉnh**. Nên con số −3,65% đo
close-to-close **gần như toàn bộ là phép trừ cơ học**, không phải bán tháo.

Bằng chứng thật cho việc người săn cổ tức thoát nằm ở **hành vi trong phiên**:
mở cửa 158.000 (cao hơn tham chiếu +5,3%) rồi bị bán suốt phiên xuống đóng cửa
chỉ cách đáy 0,13%. Kết luận P11 vẫn đúng — chỉ là bằng chứng chống đỡ nó là số
khác với số đang ghi.
