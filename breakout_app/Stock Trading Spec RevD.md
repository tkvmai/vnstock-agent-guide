# Stock Trading Spec RevD — Breakout / Lướt sóng (T+2.5)

> **Trạng thái:** BẢN NHÁP chờ duyệt. Thay thế *Stock Trading Spec RevC.docx* (giữ lại làm tài
> liệu tham chiếu lịch sử). RevD là bản đặc tả thuật toán sạch, tự chứa. Các ngưỡng điểm trong
> tài liệu này là nguồn chuẩn duy nhất cho `engine/tables.py`.
>
> **RevD thay đổi gì so với RevC — và tại sao:** RevC khuyến nghị cổ phiếu *quá muộn*. Điểm
> breakout của RevC **thưởng cho việc giá đã chạy xa** (giá đã +2% trên đỉnh được 100 điểm,
> trong khi mã vừa vượt đỉnh chỉ được 40 điểm), **không hề biết breakout xảy ra cách đây mấy
> phiên**, và **quá mua gần như không bị phạt** (RSI chỉ chiếm ~1.8% điểm cuối). RevD đưa
> **thời điểm vào lệnh (entry timing) thành yếu tố hạng nhất**: phát hiện cổ phiếu *trước khi*
> breakout, ưu tiên breakout *mới* hơn breakout đã chạy xa, phạt breakout *cũ* theo số phiên,
> và áp dụng hệ số phạt *quá nóng* thực sự. Những phần RevC làm đúng (thanh khoản, RS, dòng
> tiền, A/D, volume dry-up, chất lượng nền, sức mạnh đóng cửa) được giữ nguyên.

---

## 0. Ngữ cảnh thiết kế

- **Mục đích:** tìm cổ phiếu *có thể hành động ngay bây giờ* cho một nhịp lướt sóng T+2.5 — tức
  là hoặc **sắp breakout** (vào sớm để có vị thế), hoặc **vừa breakout và chưa chạy xa**.
- **Không phải:** phân tích cơ bản, cổ tức, tăng trưởng dài hạn, hay *đuổi* theo một con sóng
  đã chạy rồi.
- **Thực tế T+2.5:** mua hôm nay → ~2.5 phiên sau mới bán được. Nên điểm vào phải còn dư địa
  tăng *sau khi* bạn đã bị khóa. Breakout đã chạy xa = bạn mua đỉnh rồi bị khóa trong nhịp điều
  chỉnh. Đây chính là lỗi cốt lõi mà RevD sửa.

---

## 1. Layer 1 — Bộ lọc cứng (giữ nguyên như RevC)

Loại các mã không đủ điều kiện trước khi tính điểm. Các bộ lọc tĩnh chạy 1 lần/ngày; 2 bộ lọc
*động* chạy mỗi 5 phút.

| # | Bộ lọc | Điều kiện | Tần suất |
|---|--------|-----------|----------|
| 1 | Sàn | Chỉ HOSE + HNX (loại UPCOM) | tĩnh |
| 2 | Trạng thái giao dịch | không cảnh báo/kiểm soát/tạm ngừng | tĩnh |
| 3 | Lịch sử | ≥ 60 phiên | tĩnh |
| 4 | Giá tối thiểu | ≥ 5,000 VND | tĩnh |
| 5 | GTGD20 | ≥ 20 tỷ VND | tĩnh |
| 6 | Hoạt động intraday | GTGD hôm nay ≥ 30% kỳ vọng (điều chỉnh theo thời gian) | **động (5 phút)** |
| 7 | Giá trần/sàn | không ở giá trần hoặc sàn | **động (5 phút)** |
| 8 | Chặn CV | CV(GTGD, 20 phiên) < 200% | tĩnh |
| 9 | Dữ liệu sạch | OHLCV đầy đủ | tĩnh |
| 10 | **Cổng chế độ thị trường** | VN-Index không trong downtrend rõ ràng | mỗi lần quét |

**Cổng chế độ thị trường** (giữ nguyên):
```
vnindex_ma20 = mean(vnindex_close, 20);  vnindex_ma5 = mean(vnindex_close, 5)
if close/ma20 < 0.97 and ma5 < ma20:  → BLOCKED  (dừng screener; chỉ chạy khi override thủ công)
elif close/ma20 < 1.00:               → CAUTION  (vẫn chạy, kèm cảnh báo)
else:                                 → OK
```

> **RevD bổ sung cho bộ lọc #7:** một ứng viên `PRE_BREAKOUT` đang *nằm ở giá trần* thì không
> mua được → loại, giống như breakout đã xác nhận mà nằm ở trần.

---

## 2. Layer 2 — Chấm điểm BUY có ý thức về thời điểm

### 2.0 Pivot, breakout ratio, breakout age (các đại lượng thời điểm)

```
pivot            = max(close, 20 phiên gần nhất KHÔNG tính hôm nay)   # đỉnh 20 phiên trước (RevC "Close20")
breakout_ratio   = close_today / pivot
dist_below_pivot = (pivot - close_today) / pivot × 100                # chỉ dùng khi close < pivot
```

**`breakout_age`** = con sóng vượt pivot hiện tại *bắt đầu* cách đây bao nhiêu phiên
(0 = hôm nay là phiên đầu tiên đóng cửa trên đỉnh 20 phiên của chính nó):
```
Với mỗi phiên lịch sử i:  above_i = close[i] > max(close[i-20 : i])
above_today = close_today > pivot
breakout_age = (số phiên liên tiếp, tính đến hôm nay, thỏa `above`) − 1
```
Ví dụ: breakout hôm nay → age 0. Breakout hôm qua, vẫn trên đỉnh → age 1. Breakout 3 phiên
trước → age 3 (muộn).

### 2.1 Máy trạng thái (tính đầu tiên — chi phối mọi thứ)

| Trạng thái | Nhãn (UI) | Điều kiện |
|-----------|-----------|-----------|
| `BREAKOUT_FRESH` | 🟢 Mua ngay | `breakout_ratio ≥ 1.00` **và** `breakout_age ≤ 1` **và** `breakout_ratio ≤ 1.04` **và có LỰC ĐẨY**: `return_1d > 0` và `closing_strength ≥ 40%` |
| `BREAKOUT_LATE` | 🟠 Muộn | `breakout_ratio ≥ 1.00` **và** (`breakout_age ≥ 2` **hoặc** `breakout_ratio > 1.04`) |
| `PRE_BREAKOUT` | 🔵 Sắp breakout | `breakout_ratio < 1.00` **và** `dist_below_pivot ≤ 3%` **và** `dry_up_ratio < 0.9` **và** `narrowing_ratio < 0.9` **và** `ma20 > ma50` **và** `slope_ma20 > 0` **và** `rs_weighted ≥ 0` **và NỀN SẠCH**: không có phiên nào đóng trên pivot trong 3 phiên gần nhất |
| `NONE` | — | còn lại (kể cả chạm-đỉnh-không-lực-đẩy) → **loại** khỏi bảng khuyến nghị |

> **Thrust gate & nền sạch (bổ sung từ loss-review 09/07/2026** — `analysis/loss_reviews.md`**):**
> (1) Giá chỉ *chạm* đỉnh trong phiên đi ngang/đỏ với đóng cửa yếu KHÔNG phải breakout — 3/4 ca
> thua ngày 3/7 (BMP −0.58%/closing 22%, TCX 0%/25%, POW 0%) đều thuộc dạng này, trong khi cả 9
> ca thắng có TB +2.9%/81%. (2) Mã vừa breakout HỎNG rơi ngược về dưới pivot không phải "nền co
> chặt" — cấm PRE_BREAKOUT khi có phiên đóng trên pivot trong 3 phiên gần nhất (ca BMP được
> tái-KN điểm 78.5 ngay khi breakout đang chết).

Lý do: chỉ hiển thị `FRESH`, `PRE_BREAKOUT`, `LATE`. `PRE_BREAKOUT` là điểm vào *sớm nhất,
R:R tốt nhất* mà bạn yêu cầu; `LATE` chính là loại mà RevC khuyến nghị quá nhiều — giữ lại để
tham khảo nhưng bị hạ điểm để không bao giờ đứng đầu danh sách.

### 2.2 Điểm tín hiệu (Signal) — tùy theo trạng thái

Hai bộ chấm "tín hiệu" loại trừ lẫn nhau. Mỗi mã dùng đúng một bộ tùy trạng thái.

#### 2.2a Điểm Trigger (cho `BREAKOUT_FRESH` / `BREAKOUT_LATE`)

```
Trigger_raw = 0.35·price_fresh + 0.25·volume + 0.20·dry_up + 0.10·base_quality + 0.10·closing
Trigger     = Trigger_raw × age_factor
```

**`price_fresh`** — THƯỞNG cho độ MỚI, giảm dần khi giá chạy xa (thay cho band thưởng-extension của RevC):

| breakout_ratio | Điểm | Ý nghĩa |
|---|---|---|
| < 1.00 | (đã gate — không thuộc trạng thái này) | |
| 1.00 – 1.02 | 100 | Vừa vượt đỉnh — điểm vào lý tưởng |
| 1.02 – 1.04 | 80 | Vẫn còn sát pivot |
| 1.04 – 1.07 | 50 | Đang chạy xa — R:R xấu đi |
| ≥ 1.07 | 20 | Đuổi giá — tránh |

**`age_factor`** — hạ điểm breakout cũ (sửa lỗi "điểm mua đã qua 2–3 ngày"):

| breakout_age | Hệ số |
|---|---|
| 0 | ×1.00 |
| 1 | ×0.90 |
| 2 | ×0.60 |
| ≥ 3 | ×0.30 |

**`volume`** (điều chỉnh theo thời gian, giữ nguyên RevC): `volume_ratio = volume_intraday / (avg_vol_20d × time_ratio)` →
`<1.0→0, 1.0–1.3→50, 1.3–1.8→80, ≥1.8→100`.
**`dry_up`** (giữ nguyên): `pre_vol_avg(T-5..T-1) / avg_vol_20d(T-6..T-25)` →
`<0.5→100, 0.5–0.7→80, 0.7–0.9→60, 0.9–1.1→40, >1.1→20`.
**`base_quality`** (giữ nguyên): `narrowing = atr_5d/atr_20d` → `<0.5→100, 0.5–0.7→80, 0.7–0.9→60, 0.9–1.1→40, >1.1→20`.
**`closing`** (giữ nguyên): `(close-low)/(high-low)×100` → `>80→100, 60–80→80, 40–60→60, 20–40→40, <20→20` (high=low → 50).

> **Ghi chú — bỏ risk_ratio.** Hệ số phạt `risk_ratio = breakout_ratio × ATR5/close` của RevC
> bị loại: extension nay do `price_fresh` xử lý, độ cũ do `age_factor` xử lý, biến động do
> `base_quality` xử lý. Giữ risk_ratio sẽ phạt trùng.

#### 2.2b Điểm Setup (cho `PRE_BREAKOUT`)

Trả lời "mã này có đang co chặt, ngay dưới kháng cự, sẵn sàng bật lên không?" — không cần
breakout đã xác nhận.

```
Setup = 0.30·proximity + 0.25·base_quality + 0.20·dry_up + 0.15·structure + 0.10·rs
```

**`proximity`** (càng sát dưới pivot = càng sắp bật):

| dist_below_pivot | Điểm |
|---|---|
| 0 – 1% | 100 |
| 1 – 2% | 80 |
| 2 – 3% | 55 |
| ≥ 3% | 0 (cũng bị loại bởi trạng thái) |

`base_quality`, `dry_up` = cùng band như Trigger. `structure` = điểm `alignment` MA của RevC
(ma20-vs-ma50) trộn với `slope` (0.5/0.5). `rs` = điểm `score_rs` của RevC (RS vs VN-Index ×
gia tốc). Không có `age_factor` (chưa breakout).

### 2.3 Thanh khoản (giữ nguyên RevC)

```
Liquidity = 0.55·gtgd20 + 0.30·intraday + 0.15·cv
```
gtgd20 qua `safety_ratio = GTGD20 / position_size` (`<10→0,10–20→20,20–50→40,50–100→60,100–200→80,≥200→100`);
intraday_ratio (`<30→0…≥200→100`); CV (`<30→100…≥150→0`). Toàn bộ đúng như RevC mục 3.1.

### 2.4 Động lượng (giữ nguyên RevC — bối cảnh xu hướng)

```
Momentum = 0.30·composite + 0.20·ma + 0.20·rs + 0.20·flow + 0.10·technical
```
(composite = returns đa khung × consistency; ma = price_vs_ma20/50 + alignment + slope; rs vs
VN-Index; flow = A/D + dòng tiền thông minh × convergence; technical = RSI + MACD hist). Toàn
bộ band RevC mục 3.2 giữ nguyên. Động lượng nay chỉ cung cấp *bối cảnh xu hướng* — thời điểm
nằm ở lớp Signal + overheat, nên việc động lượng thưởng cho mã đã tăng không còn tự gây ra vào
lệnh muộn nữa.

### 2.5 Hệ số quá nóng / thời điểm (MỚI — sửa lỗi quá mua thực sự)

Áp dụng cho BUY cuối của **mọi** trạng thái. Khiến quá mua/chạy xa thực sự tốn 25–55% điểm
(so với ~1.8% của RevC).

```
overheat_mult = rsi_mult × extension_mult
```

**`overhead_mult` (bổ sung 11/07 — loss-review P10):** phạt breakout còn sâu dưới đỉnh dài hạn
(~4 tháng = toàn bộ lịch sử fetch): vượt đỉnh 20 phiên nhưng phía trên đầy người kẹp hàng chờ
bán. Kiểm chứng 143 tín hiệu: retT3 TB đơn điệu theo khoảng cách tới đỉnh (<−20% → −2.11%;
sát đỉnh → −0.34%, win 39%). `dist_to_high = close/max(close, ~100 phiên) − 1`:

| dist_to_high | overhead_mult |
|---|---|
| ≥ −5% | ×1.00 |
| −10% … −5% | ×0.90 |
| < −10% | ×0.70 |

| RSI(14) | rsi_mult | | price_vs_ma20 | extension_mult |
|---|---|---|---|---|
| ≤ 70 | ×1.00 | | ≤ 6% | ×1.00 |
| 70 – 75 | ×0.90 | | 6 – 9% | ×0.85 |
| 75 – 80 | ×0.75 | | 9 – 13% | ×0.65 |
| > 80 | ×0.55 | | > 13% | ×0.45 |

(RSI(14) tính theo giá đóng cửa ngày, có ghép giá close live, như RevC mục 3.2.5.)

### 2.6 Điểm BUY cuối cùng

```
Signal   = Trigger   (nếu trạng thái ∈ {FRESH, LATE})
         = Setup     (nếu trạng thái == PRE_BREAKOUT)

BUY_raw  = 0.35·Liquidity + 0.25·Momentum + 0.40·Signal
BUY      = BUY_raw × overheat_mult × overhead_mult × state_mult
```

**`state_mult`** — xếp fresh/pre-breakout trên late, loại none:

| Trạng thái | state_mult |
|---|---|
| `BREAKOUT_FRESH` | ×1.00 |
| `PRE_BREAKOUT` | ×0.95 (sớm nhất, R:R tốt nhất — cố ý giữ sát fresh) |
| `BREAKOUT_LATE` | ×0.60 (bị hạ — không bao giờ là top pick) |
| `NONE` | ×0.00 (loại) |

Nhờ vậy có **một điểm BUY so sánh được** để bảng sắp xếp hợp lý: fresh ≳ pre-breakout > late.

### 2.7 Bảng xếp hạng (ngưỡng giữ nguyên, đầu vào có ý thức thời điểm)

| BUY | Xếp hạng | Hành động |
|---|---|---|
| 85–100 | Rất mạnh | Ưu tiên cao nhất |
| 75–84 | Mạnh | Theo dõi sát |
| 65–74 | Khá | Watchlist |
| 50–64 | Trung bình | Không ưu tiên |
| < 50 | Yếu | Bỏ qua |

---

## 3. Bảng tần suất cập nhật

| Đại lượng | Tần suất | Lý do |
|---|---|---|
| Layer-1 tĩnh (bộ lọc 1–5,8,9), pivot, breakout_age, MA/slope, RS, dry_up, base_quality, A/D, flow | 1 lần/ngày (EOD/warmup) | dẫn xuất EOD; cố định trong ngày |
| Layer-1 động (#6, #7), close, breakout_ratio, price_fresh, volume_ratio, closing, RSI, overheat_mult, state, BUY | mỗi 5 phút | phụ thuộc giá/volume live |

---

## 4. Theo dõi & phản hồi (triển khai ở Phase 3–4 — tóm tắt tại đây)

- **Kiểm chứng (Phase 3):** khi một mã lần đầu vào bảng khuyến nghị trong ngày (BUY ≥ ngưỡng
  khuyến nghị), ghi lại `reco_close` (hiển thị). Return đo close-to-close trong `ohlcv_daily`
  (cùng hệ điều chỉnh — chống lỗi corporate action): `ret_Tn = close(reco+n)/close(reco) − 1`.
  **Ba thước đo, vai trò cố định trước** (tránh "chọn số đẹp"):
  - `win_T3` — **chính**: rủi ro bị khóa T+2.5 (điểm thoát sớm nhất); nhãn cho learner.
  - `win_T5` — phụ: đúng khung swing 1 tuần của spec.
  - `MFE ≥ 3%` trong 5 phiên — phụ: có cơ hội chốt lãi thật hay không.
- **Tự học trọng số (Phase 4):** hệ thống **tự** theo dõi tỷ lệ thắng T+3 (`win_t3`) và **tự
  điều chỉnh 3 trọng số `W_BUY`** (Thanh khoản / Động lượng / Tín hiệu) theo thành phần nào thực
  sự dự báo thắng (tương quan point-biserial với `win_t3`) — không cần feedback tay. **Có kiểm
  soát:** blend từ trọng số MẶC ĐỊNH theo `alpha` tăng dần theo số mẫu, mỗi trọng số lệch tối đa
  **±0.10** so với mặc định, cần ≥ 20 tín hiệu đủ T+3, loại mã 'không mua được'. Lưu ở
  `data/learned_weights.json` (đảo ngược — xóa file / tắt `USE_LEARNED_WEIGHTS`); **không bao giờ
  sửa `config.py`**. Chạy tự động ở phiên EOD 15:30 (`analysis/learn_weights.py`); engine dùng
  `config.get_w_buy()`.
  - **Học từ TOÀN BỘ pool Layer-1 (không thiên lệch):** mỗi phiên EOD snapshot *mọi* mã qua lọc
    cứng (kể cả mã KHÔNG khuyến nghị, `state=NONE`) vào `daily_observations`, giá đóng cửa làm mốc
    → đo T+3 close-to-close. Nhờ đó learner thấy cả mã app bỏ sót nhưng vẫn lãi (tránh "tự khen").
- **Báo cáo chất lượng khuyến nghị:** `analysis/calibrate.py` in win-rate mã được-KN vs không-KN
  và theo trạng thái → cho biết gate FRESH/PRE có thêm edge hay đang bỏ sót. Chỉ báo cáo, KHÔNG
  tự đổi gate (đó là quyết định của bạn).
- ~~Feedback tay~~ — đã bỏ khỏi UI: nhãn thắng/thua lấy tự động từ T+3 trên toàn pool nên việc
  chấm tay là thừa. (Bảng `feedback` trong DB giữ lại để tương thích; có thể khôi phục nếu cần.)

---

## 5. Các tham số mở cần xác nhận trước khi triển khai

Đây là các knob RevD đáng kiểm tra nhất (tất cả nằm trong `config.py`):

1. **Ngưỡng extension của FRESH** = `breakout_ratio ≤ 1.04` và **age ≤ 1**. (Chặt hơn = ít mã
   hơn nhưng vào sớm hơn.)
2. **Cửa sổ proximity của PRE_BREAKOUT** = trong vòng 3% dưới pivot. (Rộng hơn = nhiều/sớm hơn.)
3. Đường cong **age_factor** (0→1.0, 1→0.9, 2→0.6, ≥3→0.3) và **overheat** (RSI/extension).
4. **state_mult**: PRE_BREAKOUT ×0.95 vs FRESH ×1.0 — mức độ ưu ái vào lệnh sớm.
5. **Trọng số Signal** trong BUY = 0.40 (vs Thanh khoản 0.35, Động lượng 0.25).
