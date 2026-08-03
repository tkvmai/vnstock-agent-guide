# Hồ sơ review các mã BỎ SÓT (missed-winner post-mortem)

> Đối tượng: mã **không được khuyến nghị** (is_reco=0) nhưng **thắng ≥ `MISS_MIN_RET_T3` (3%)**
> tại T+3 — false negative của screener. Nguồn: tab "📊 Bỏ sót — Toàn pool" /
> `db.unreviewed_misses()`. Sau khi review từng ca → `db.mark_miss_reviewed(sym, ngày, kết_luận)`.
>
> Quy trình: chart TradingView + dữ liệu vnstock → trả lời "vì sao bị loại, và việc loại đó
> ĐÚNG hay SAI?" Chỉ khi một lý do loại-sai LẶP LẠI (≥3 ca) mới đề xuất nới công thức, kiểm
> chứng trên `daily_observations` trước khi áp dụng — nới gate làm tăng số mã được khuyến nghị,
> phải chắc chắn win-rate nhóm thêm vào không kéo tụt tổng thể.
>
> **Phân loại kết luận:** `loại_hợp_lệ` (thắng nhờ may/tin tức — không sửa gì) ·
> `thrust_gate_quá_chặt` · `cửa_sổ_pre_quá_hẹp` (coil >3% dưới đỉnh) · `ngưỡng_điểm_cao` ·
> `muộn_nhưng_vẫn_chạy` (LATE mà thắng — cân nhắc lại state_mult 0.6) · `khác`

---

## Đợt review 19/07/2026 — 26 ca bỏ sót đầu tiên (quan sát 09-14/07)

**Kết luận tập thể: `loại_hợp_lệ` — cả 26 ca là một pattern duy nhất: đảo chiều đáy V,
ngoài phạm vi thiết kế của hệ breakout.**

- **Cấu trúc chung:** 100% ca là state NONE / BUY 0.0 — giữa sự kiện điều chỉnh 9-15/7 mọi
  mã đều sâu dưới pivot 20 phiên, cấu trúc MA gãy, RS âm → không thể là FRESH/PRE theo bất
  kỳ định nghĩa breakout nào (kể cả PRE đã nới 1.05). Người thắng lớn (HCM +8.6%, PVD +7.0%,
  ACB +5.8%…) là các mã bật mạnh nhất từ đáy 13/7 — nhóm chứng khoán/dầu khí beta cao.
- **Base rate phán quyết:** trong CÙNG cửa sổ, toàn pool trung bình ÂM (9/7: −1.75% · 10/7:
  −2.31% · 13/7: −0.16% · 14/7: −2.31%), chỉ 3-16% số mã đạt ≥3%. Tức "mua đáy" các ngày đó
  về kỳ vọng là THUA — các ca bỏ sót là đuôi phải may mắn, không phải tín hiệu bị lọc oan.
  Nhịp bật 16/7 còn TẮT ngay 17/7 (pool 14/7 → T+3 âm trở lại) — đúng kiểu bẫy hồi kỹ thuật.
- **Hệ thống đứng ngoài là ĐÚNG:** regime blocked + health 19-37 (⛔ halt) trong toàn bộ cửa
  sổ; với ràng buộc khóa T+2.5, bắt dao rơi ngày 13/7 rủi ro thêm một chân giảm là có thật
  (và đã xảy ra ngày 17/7).

**Khoảng trống chiến lược được ghi nhận (không phải lỗi):** hệ RevD không có và không định
có năng lực bắt đáy V — đó là một chiến lược khác (mean-reversion) với edge/rủi ro khác hẳn.
Nếu muốn, phải là quyết định sản phẩm mở kênh mới với spec riêng — số liệu base rate ở trên
hiện KHÔNG ủng hộ (kỳ vọng âm nếu vào bừa; cần tiêu chí chọn lọc riêng chưa được nghiên cứu).
