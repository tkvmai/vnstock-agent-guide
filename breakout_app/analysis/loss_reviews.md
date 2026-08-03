# Hồ sơ review các ca Thua (loss post-mortem)

> Quy trình: mỗi ca Thua (win_t3=0) được chẩn đoán bằng chart TradingView + dữ liệu vnstock,
> phân loại nguyên nhân chuẩn hóa. Khi một nguyên nhân LẶP LẠI (≥3 ca) → đề xuất sửa công thức,
> kiểm chứng trên dữ liệu `daily_observations` trước khi áp dụng.
>
> **Phân loại nguyên nhân:** `thị_trường_đỏ` · `không_lực_đẩy` (breakout chạm đỉnh phiên
> ngang/đỏ, không có thrust) · `breakout_hỏng_tái_KN` (breakout fail rồi bị khuyến nghị lại như
> pre-breakout) · `climax_kiệt_sức` · `tin_tức` · `kháng_cự_dài_hạn` · `lỗi_dữ_liệu` · `khác`
>
> **Sổ đăng ký:** mỗi ca đã review được ghi vào bảng **`loss_reviews`** trong `data/screener.db`
> (`db.mark_loss_reviewed`). Bắt đầu một đợt review mới bằng **`db.unreviewed_losses()`** — chỉ
> trả các ca Thua (win_t3=0) CHƯA có trong sổ, nên không bao giờ review trùng.

---

## Đợt review 09/07/2026 — các ca Thua ngày KN 03/07 (thứ Sáu)

**Bối cảnh thị trường:** VN-Index đỏ −1.00% ngày 06/07 (phiên T+1) — kéo mọi mã xuống. Nhưng
9 mã Thắng cùng đợt vẫn phục hồi và có lãi T+3 → yếu tố thị trường KHÔNG phải nguyên nhân
phân biệt; cái phân biệt là **chất lượng lực đẩy ngày khuyến nghị**:

| Nhóm | chg% ngày KN (TB) | closing strength (TB) |
|---|---|---|
| 9 mã Thắng | **+2.91%** | **81%** |
| 4 mã Thua | **+0.16%** | **45%** |

### BMP — Thua −5.04% (ca nặng nhất) · phân loại: `không_lực_đẩy` + `breakout_hỏng_tái_KN`
- Ngày KN 3/7: phiên **ĐỎ −0.58%**, closing strength **22%** (đóng sát low), ratio 1.0019 (age 1)
  → "breakout" đã đang fade nhưng vẫn được gán FRESH vì chỉ xét `ratio ≥ 1.0`.
- Chart TradingView: đỉnh của một sóng dài từ ~128k; indicator của user đánh dấu **TRAP ×2 ngay
  vùng đỉnh 155k**; sau đó phân phối rõ (nến đỏ volume tăng: 8/7 143k CP, 9/7 199k CP so 7/7 60k).
- **Lỗi kép:** 4–6/7 giá rớt về dưới pivot → thuật toán lại nhận nó là `PRE_BREAKOUT` điểm cao
  (78.5) — không phân biệt "nền co chặt chờ vượt" với "breakout vừa hỏng rơi ngược về".

### MCH — Thua −3.05% · phân loại: `climax_kiệt_sức`
- 1/7: nến climax +5% với volume ~884k (gấp ~4 lần bình thường) — chart đánh dấu TRAP ngay đỉnh.
- 3/7 KN ở nhịp re-test (ratio 1.0028, RSI 69.7 — vừa KHÍT dưới ngưỡng phạt 70) → mua đúng lúc
  kiệt sức, fade 4 phiên liền. Bài học: climax volume trước đó + RSI sát 70 là vùng xám;
  cần thêm dữ liệu trước khi chỉnh band (mới 1 ca).

### TCX — Thua −1.66% · phân loại: `không_lực_đẩy`
- Ngày KN: chg **0.00%**, closing 25%, ratio 1.0011 — giá đi ngang *chạm* đỉnh 5 phiên liền
  (44,400–45,000) chứ không hề vượt bằng lực mua. Phiên 6/7 thị trường đỏ → gãy −3.33%.

### POW — Thua −1.34% · phân loại: `không_lực_đẩy`
- Ngày KN: chg **0.00%**, ratio đúng 1.0000 — chạm đỉnh, không thrust. Thị trường đỏ kéo xuống.

### VJC — Thua −2.05% (KN 4-5/7 = bản ghi cuối tuần) · phân loại: `lỗi_dữ_liệu` + rủi ro PRE thông thường
- Pre-breakout không nổ sau phiên thị trường đỏ — nằm trong rủi ro chấp nhận được của tín hiệu
  vào sớm. Bản ghi 4-5/7 là artifact cuối tuần (xem P4).

### Lỗi dữ liệu phát hiện kèm (P4): bản ghi trùng ngày cuối tuần
- **4/7 và 5/7/2026 là Thứ Bảy & Chủ Nhật** — scan chạy cuối tuần (khởi động app) dùng dữ liệu
  thứ Sáu → tạo tín hiệu trùng lặp, làm sai thống kê win-rate (đếm đôi).

---

## Đề xuất & hành động (đợt 09/07/2026)

| # | Pattern (số ca) | Sửa đổi | Trạng thái |
|---|---|---|---|
| P1 | `không_lực_đẩy` (3: BMP, TCX, POW) | **Thrust gate cho FRESH**: ngày KN phải có `return_1d > 0` VÀ `closing_strength ≥ 40` — không thì KHÔNG coi là breakout (NONE). Kiểm chứng trên 3/7: giữ 6/6 mã FRESH thắng, loại 3/4 mã FRESH thua. | ✅ Áp dụng (RevD 2.1) |
| P2 | `breakout_hỏng_tái_KN` (1: BMP, nhưng lỗi logic rõ) | **PRE_BREAKOUT phải là nền sạch**: không có phiên nào đóng cửa trên pivot trong 3 phiên gần nhất (loại breakout vừa hỏng rơi về). | ✅ Áp dụng (RevD 2.1) |
| P3 | `thị_trường_đỏ` (nền chung) | Không đổi công thức (mã thắng vẫn sống qua phiên đỏ). Theo dõi thêm: cân nhắc nâng ngưỡng alert khi regime=CAUTION nếu pattern lặp. | 👁 Theo dõi |
| P4 | `lỗi_dữ_liệu` cuối tuần | Chặn ghi tín hiệu/observation ngày nghỉ; xóa bản ghi 4-5/7 (có backup DB). | ✅ Sửa code + dọn dữ liệu |
| P5 | `climax_kiệt_sức` (1: MCH) | Chưa đủ ca. Ứng viên nếu lặp: phạt khi có phiên volume climax (>3× TB) trong 3 phiên trước KN, hoặc hạ ngưỡng RSI phạt 70→68. | 👁 Theo dõi |
| P6 | `lỗi_dữ_liệu` — điều chỉnh giá (PET 6/7: −31% giả, thực +3.5%) | Provider viết lại lịch sử theo giá điều chỉnh sau ex-date → entry chưa điều chỉnh vs forward đã điều chỉnh. Sửa: return tính close-to-close hoàn toàn trong `ohlcv_daily` (cùng hệ điều chỉnh, tự lành). | ✅ Sửa code |

> **Lưu ý hệ quy chiếu (từ 09/07):** kết quả T+3 đo **close ngày KN → close T+3** (chuẩn hóa,
> chống lỗi điều chỉnh giá). Giá KN intraday vẫn hiển thị để tham khảo nhưng không dùng làm mẫu
> số return. Vì vậy mã chạy mạnh trong phiên KN (ORS, HVN...) có return T+3 thấp hơn con số đo
> từ giá alert — cohort 3/7 sau chuẩn hóa: 6/13 thắng (4 ca đã review vẫn nguyên kết luận Thua).

---

## Đợt review 09/07/2026 (lần 2) — 9 ca còn lại của cohort 3/7 + 6/7

**Kết luận chính: KHÔNG cần sửa công thức mới — 3 ca thua đáng kể đều rơi đúng vào 2 pattern
ĐÃ VÁ trong đợt 1, xác nhận fix đúng hướng:**

| Mã (ngày) | ret T+3 | Chẩn đoán | Pattern |
|---|---|---|---|
| POW (6/7) | −4.05% | KN trong phiên **đỏ −0.67%** chạm đỉnh (ratio 1.0000, lần thứ 2 của POW); volume 20M cao bất thường trên phiên đỏ = phân phối; 9/7 gãy −3.4% đóng sát low | `không_lực_đẩy` — **thrust gate (P1) sẽ loại** |
| VHM (6/7) | −3.50% | Phiên KN +1.65% NHƯNG closing **30% <40** — kéo lên rồi fade cuối phiên (bóng trên dài, chart đánh TRAP), volume spike = xả vào breakout; BUY chỉ 54.4 (sát ngưỡng) | `không_lực_đẩy` (biến thể đóng-cửa-yếu) — **thrust gate (P1) sẽ loại** |
| BMP (6/7) | −2.35% | Lặp lại đúng ca BMP đợt 1: breakout hỏng rơi về dưới pivot rồi được tái-KN là PRE_BREAKOUT | `breakout_hỏng_tái_KN` — **clean-coil (P2) sẽ loại** |
| ORS (3/7) | −1.71% | **Alert thực ra ĐÚNG**: báo lúc vượt đỉnh 13,900, T+3 từ giá alert = **+3.6%**. Thua chỉ theo hệ close-to-close vì phiên KN đã chạy +6.9% → người mua ĐUỔI cuối phiên mới thua (T+1 rung lắc −5.1% về đúng pivot rồi bật lại) | `đuổi_giá_cuối_phiên` — caveat hệ quy chiếu, 👁 theo dõi |
| VPB 3/7 · MSB 3/7 · VPB 6/7 · NAB 6/7 · CTG 6/7 | −0.7%…0.0% | Quanh hòa vốn (<1%), phần lớn thuộc cohort 6/7 dính phiên thị trường đỏ; NAB RSI 70.6 đã bị overheat_mult hạ điểm đúng | `nhiễu_hoà_vốn` — không hành động |

**Cập nhật đếm pattern:** `không_lực_đẩy` = **5 ca** (BMP, TCX, POW×2, VHM) — thrust gate đã vá,
các ca mới đều xác nhận; `breakout_hỏng_tái_KN` = **2 ca** (BMP×2) — clean-coil đã vá.
**Bài học vận hành:** cohort 6/7 sinh tín hiệu TRƯỚC khi 2 fix có hiệu lực — cohort từ 10/07 trở
đi mới đo được chất lượng rule mới.

---

## Đợt review 11/07/2026 — cohort 07/07 (9 ca thua / 11 tín hiệu)

**Chẩn đoán chung: KHÔNG phải lỗi tín hiệu — là rủi ro thị trường + rủi ro cụm ngành.**

- **Tín hiệu về mặt kỹ thuật là ĐÚNG chuẩn:** cả 5 mã FRESH thua (FTS, SHS, VND, MBS, MBB) đều
  có thrust hoàn hảo ngày KN (+1.8%…+7.2%, closing 97–100%) — thrust gate mới vẫn giữ chúng,
  và giữ là đúng (cùng dạng setup đã thắng ở cohort 3/7 & 6/7).
- **Thứ giết cohort là THỊ TRƯỜNG quay đầu:** VN-Index đỏ 2 phiên liên tiếp 9/7 (−0.70%) và
  10/7 (−0.67%) — rơi đúng cửa sổ T+2..T+3. Lúc KN (7/7) regime = "ok" (không thể biết trước).
  Nhóm chứng khoán beta cao khuếch đại mức giảm (FTS −5.3%, SHS −4.2%, VND −4.0%).
- **Rủi ro cụm ngành (quan sát MỚI — P7):** 4/5 mã FRESH thua cùng thuộc nhóm **chứng khoán**
  (FTS/SHS/VND/MBS). App khuyến nghị một cụm tương quan cao → một cú đảo chiều ngành quét sạch
  cả danh sách. SHS còn được KN lặp lại nhiều ngày liên tiếp ở vùng đỉnh đi ngang.
- BMP lần thứ 3: `breakout_hỏng_tái_KN` — clean-coil đã vá. MSB/VJC PRE không nổ khi thị trường
  quay đầu (rủi ro vốn có của vào sớm); ACB nhiễu hoà vốn.

| # | Pattern (đếm lũy kế) | Nhận định | Trạng thái |
|---|---|---|---|
| P3 | `thị_trường_đỏ` (giờ là nguyên nhân chủ đạo: ~8 ca qua 2 cohort) | Regime gate là phòng tuyến thiết kế nhưng chỉ đo TẠI thời điểm KN — không đỡ được đảo chiều 2-3 phiên sau. Đây là beta risk vốn có của swing T+2.5; phòng thủ đúng là kỷ luật stop-loss + đa dạng ngành (P7), KHÔNG phải siết thêm scoring. | 👁 Theo dõi |
| P7 | `cụm_ngành` (MỚI, 1 đợt: 4 mã CK cùng lúc) | Ứng viên cải tiến ở tầng ALERT (không đụng scoring): giới hạn tối đa ~2 mã/ngành trong top-N gửi Telegram, phần còn lại lấy mã ngành khác kế tiếp. Chờ thêm 1-2 đợt lặp lại rồi đề xuất cụ thể. | 👁 Theo dõi |

**Vận hành:** app không chạy lúc 15:30 ngày 10/7 → outcome bị trễ, phải tính bù tay sáng 11/7.
Nên để `run.py` chạy qua 15:30 các ngày giao dịch (job sáng 08:00 cũng tự bù nếu app được bật).

---

## Đợt review 15/07/2026 — lứa 08/07 (16 ca) + 09/07 (9 ca): sự kiện điều chỉnh toàn thị trường

**Chẩn đoán chung (25/25 ca): `thị_trường_đỏ` — một sự kiện vĩ mô duy nhất.** VN-Index rơi
1853.7 (8/7) → 1783.6 (15/7) = **−3.8% trong 5 phiên** (−0.70, −0.67, −1.52, +0.34, −1.28).
Cửa sổ T+1..T+3 của cả hai lứa nằm trọn trong chuỗi giảm; **MFE gần như toàn bộ ÂM** — bị
khóa lỗ từ T+1, đúng kịch bản rủi ro T+2.5 ở quy mô lớn nhất từ khi chạy app.

- **Không phải lỗi tín hiệu:** setup 8/7 giống hệt dạng đã thắng trong uptrend tháng 6;
  regime lúc KN 8/7 = "ok" (index 1853, sát đỉnh, phiên +0.29%) — không quy tắc per-stock nào
  thấy trước cú trượt 5 phiên sau đó. Gate regime phản ứng có trễ theo thiết kế (chuyển
  caution 9/7, blocked ~13/7 khi ratio < 0.97; hiện 0.9664 — app đang đúng đắn im lặng).
- **Cụm ngành CK lặp lần 2 (P7):** 11/25 ca là công ty chứng khoán (BSI −9.9, SHS −9.8/−7.9,
  FTS −7.2, VCI −6.7/−6.8, VND −6.4, TCX −5.8, ORS −5.6, MBS −5.5/−4.1) — beta cao khuếch đại
  gấp ~2 lần mức giảm index. Alert 8/7 chứa 4/8 mã CK. Ước lượng nếu áp trần 2 mã CK/danh sách
  alert: các slot CK dôi ra thay bằng ngân hàng/khác (lỗ TB ~−3% thay vì ~−7%) → giảm ~nửa
  thiệt hại phần vượt trội, nhưng KHÔNG tránh được lỗ (sự kiện toàn thị trường).
- **Lọc index-đỏ-intraday: bằng chứng vẫn trái chiều.** 9/7 KN khi index −0.7% intraday → 8/9
  thua (PET +3.5 thắng); nhưng 6/7 cũng KN trong phiên −1.0% → thắng 9/15. Không làm rule.

**Hành động:** P7 **ĐÃ TRIỂN KHAI (user duyệt 15/07)** — trần `ALERT_MAX_PER_SECTOR = 2`
mã/ngành (vi_sector ICB-2 từ screener) trong top-N alert (`scheduler._select_top_diversified`);
slot dôi ra đôn mã ngành khác kế tiếp; ngành không xác định không bị chặn; alert hiển thị thêm
ngành. Chỉ tầng alert — scoring và dashboard không đổi. Mọi thứ khác: không đổi công thức
(beta risk; phòng thủ đúng là regime gate đã hoạt động + stop-loss).

---

## Audit độ trễ 11/07/2026 (`analysis/lateness_audit.py` — chạy lại định kỳ)

Trả lời câu hỏi "app còn đề xuất muộn không?" bằng cách nhìn lại lịch sử từng mã đã đề xuất:

1. **Vấn đề muộn gốc (RevC) đã hết:** 55 tín hiệu FRESH đều ở age 0-1, giá lúc đề xuất chỉ
   **TB +0.20% / tối đa +0.83%** trên đỉnh — app bắn ngay tại điểm vượt. (Lưu ý: age≤1 là do
   luật RevD chặn sẵn — bằng chứng thực nghiệm nằm ở run-up nhỏ.)
2. **P8 (theo dõi — BẰNG CHỨNG TRÁI CHIỀU, không làm rule): đề xuất lặp ngày thứ 3+.**
   Lứa RevD (3-10/7): lần 3+ = 0/5 thắng, TB −2.98%. NHƯNG audit hồi tố lứa RevC (17/6-2/7,
   104 alert) cho kết quả NGƯỢC LẠI: lần 4+ = **55% thắng, TB +0.85%** (tốt nhất các nhóm) —
   vì trong uptrend tháng 6, leader lặp lại tiếp tục chạy. → Hiệu ứng lặp-đề-xuất **phụ thuộc
   pha thị trường** (uptrend: leader lặp = tốt; thị trường quay đầu: lặp ngày 3+ = mua cuối).
   KHÔNG làm rule cứng; nếu làm gì thì phải gắn với regime — cần nhiều dữ liệu hơn.
2b. **Audit hồi tố lứa RevC (17/6→2/7, từ `sent_alerts` + `ohlcv_daily`): 104 tín hiệu / 45 mã.**
   Xác nhận ĐỊNH LƯỢNG lời phàn nàn "đề xuất muộn" của RevC và hiệu quả của RevD:
   - RevC alert khi giá đã chạy **TB +1.64% / tối đa +6.83%** trên đỉnh; **13/73 (18%) alert ở
     age ≥ 2** (muộn thật sự vài ngày). RevD: TB +0.20% / max +0.83% / 0 ca age≥2 → cải thiện ~8×.
   - Kết quả T+3 lứa RevC: win **32%**, TB **−0.86%** (dù VN-Index tháng 6 đang lên) — mua ở giá
     đã chạy xa tạo drag hệ thống. Lứa RevD (39 tín hiệu đủ T+3): win ~44% dù dính đợt quay đầu
     9-10/7. Gợi ý cải thiện nhưng CHƯA kết luận được (khác pha thị trường, mẫu nhỏ).
2c. **Review từng-ca top 10 thua nặng nhất lứa RevC (11/07)** — kiểm tra pattern bị bỏ sót:
   - 4/10 ca (DXG, PET, PVT, DGC-23/6): **lỗi cấu trúc RevC — alert mã CHƯA breakout** (dưới
     pivot, có ca phiên đỏ closing 0-29%): RevC cho breakout=0 nhưng 0.35·TK + 0.30·ĐL vẫn đủ
     vượt ngưỡng alert. RevD đã vá tận gốc bằng state machine (NONE bị loại).
   - 3/10 ca (VIC, PVD age 2; DGC-22/6 chạy +6.2% lúc alert): muộn kiểu RevC — age/price_fresh
     /LATE đã vá. LPB: closing 17% + volume churn — thrust gate đã vá.
   - **P10 (MỚI — ĐÃ SỬA): breakout dưới bóng kháng cự dài hạn (overhead supply).** 6/10 ca
     thua nặng nằm SÂU ≥20% dưới đỉnh ~4 tháng (DGC −36/−37.6%, PVT −27.5%, PVD −20.9%,
     PC1 −20.8%, PET −20.3%) — vượt đỉnh 20 phiên nhưng trên đầu là cả vùng người kẹp hàng.
     **Kiểm chứng 143 tín hiệu toàn lịch sử:** retT3 TB đơn điệu theo khoảng cách tới đỉnh:
     <−20% → −2.11% · −20..−10% → −1.18% (win 20%) · −10..−3% → −0.54% · sát đỉnh → −0.34%
     (win 39%); tương quan +0.24. → Thêm **`overhead_mult`** nhân vào BUY: dist <−10% → ×0.70;
     −10..−5% → ×0.90; ≥−5% → ×1.0 (`tables.overhead_mult`, `dist_to_high` từ max 100 phiên
     lịch sử, hiển thị trong drill-down). Phạt mềm, không gate cứng — đảo ngược được.
   - VHM 26/6 — **ĐÃ GIẢI (điều tra tin tức 11/07): `sự_kiện_cổ_tức` → P11.** Chuỗi sự kiện:
     22/6 cả họ Vin tăng ~+7% (tin liên danh Vinhomes–VinSpeed làm tổng thầu EPC 5 dự án đường
     sắt đô thị Hà Nội); **26/6 là phiên CUỐI CÙNG hưởng quyền cổ tức tiền mặt 6,000đ/cp (60%
     — đợt chi cổ tức tiền mặt lớn nhất lịch sử TTVN ~25,000 tỷ; GDKHQ 29/6)** → cú "+3.52%
     volume gấp đôi" mà screener đọc là breakout thrust thực chất là **dòng tiền săn cổ tức
     phiên chốt quyền**; 29/6 người săn cổ tức thoát → −3.65% đóng cửa sát low. Screener mù
     hoàn toàn với sự kiện quyền — không đọc tin/lịch sự kiện.

| # | Pattern | Nhận định | Trạng thái |
|---|---|---|---|
| P11 | `sự_kiện_cổ_tức/quyền` trong cửa sổ KN (2 ca liên quan trong 3 tuần: VHM cổ tức tiền mặt 26/6→GDKHQ 29/6 thua; PET điều chỉnh ~2/3 ngay 7/7 — thắng nhưng làm nhiễu đo lường) | Screener không biết lịch chốt quyền → thrust giả từ dòng tiền săn cổ tức + giá điều chỉnh trong cửa sổ T+3. Ứng viên fix (tầng alert, không đụng scoring): tra lịch sự kiện (vnstock `Company.events`/API sự kiện) cho các mã top-N; **gắn cảnh báo "⚠️ chốt quyền ngày X"** vào alert, tùy chọn hạ ưu tiên khi GDKHQ rơi trong T+3. | 👁 Chờ quyết |

3. **P9 (MỚI — theo dõi): kênh PRE bắt sớm chỉ 4/26 mã (15%).** Replay phiên trước ngày vượt
   của 26 mã: 0 mã đủ điều kiện PRE hôm trước. Nguyên nhân: 7/26 gap thẳng qua đỉnh (không thể
   báo trước); phần còn lại bị chặn chủ yếu bởi cặp điều kiện VCP **narrowing≥0.9 (16)** và
   **dry_up≥0.9 (12)** → breakout ở VN thường xuất phát từ nền "ồn" chứ không phải nền VCP
   sách giáo khoa. Ứng viên fix: nới 2 điều kiện này (hoặc chuyển thành điểm thay vì gate cứng)
   — NHƯNG chỉ quyết sau khi `daily_observations` tích lũy ~2 tuần để backtest được tỷ lệ
   "coil ồn → vượt thành công" so với "coil chặt → vượt thành công".
