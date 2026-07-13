# Vnstock Pipeline - Tài Liệu Hướng Dẫn

Chào mừng bạn đến với tài liệu hướng dẫn sử dụng thư viện **vnstock_pipeline**. Đây là công cụ chuyên nghiệp giúp bạn xây dựng các hệ thống thu thập, xử lý và xuất dữ liệu chứng khoán tự động theo mô hình chuẩn hóa: **Fetcher → Validator → Transformer → Exporter**, kết hợp với **Kiến trúc Lưu trữ Tập trung** và công cụ CLI mạnh mẽ.

---

## 🚀 Bắt Đầu Nhanh

**Cài đặt:**

```bash
pip install -U vnstock_pipeline
```

**Cách 1: Khởi động nhanh bằng công cụ dòng lệnh (CLI - Khuyên dùng):**

Bạn có thể chạy ngay các pipeline dựng sẵn mà không cần viết code:

```bash
# Tải dữ liệu OHLCV cho nhóm cổ phiếu tự động (VD: vn30) và lưu tự động vào DB
python -m vnstock_pipeline.cli run ohlcv --watchlist vn30 --mode daily

# Hiển thị thống kê cấu trúc dữ liệu đang lưu
python -m vnstock_pipeline.cli storage preview
```

**Cách 2: Sử dụng qua Python Script:**

```python
from vnstock_pipeline.tasks.ohlcv import run_task

# Tải dữ liệu giá cho danh sách mã (tự động sử dụng cấu hình Config Hub)
run_task(['VCB', 'FPT', 'HPG'], start="2024-01-01", end="2026-06-17")
print("✅ Dữ liệu đã lưu thành công vào CSDL Vnstock (VD: ~/vnstock_db/ohlcv/)")
```

---

## 📑 Mục Lục Tài Liệu

Bộ tài liệu này đã được tinh chỉnh để hỗ trợ bạn đi từ mức độ làm quen cơ bản đến việc vận hành hệ thống dữ liệu thực tế:

1. **[Tổng Quan Kiến Trúc & Luồng Xử Lý](01-overview.md)**
   Cái nhìn toàn cảnh về kiến trúc lõi, **Config Hub (`pipeline.toml`)**, bộ kiểm duyệt Data Quality, Schema Guard và triết lý thiết kế của thư viện.
2. **[Các Tác Vụ Sẵn Có & Cách Xây Dựng](02-tasks-and-builders.md)**
   Hướng dẫn chi tiết sử dụng các Pipeline tích hợp: Dữ liệu Cơ sở, OHLCV, Báo cáo tài chính, Tin tức, Sự kiện doanh nghiệp, Giao dịch Intraday & Bảng giá trực tuyến.
3. **[Các Mẫu Thiết Kế Quy Trình Tùy Chỉnh](03-custom-pipelines.md)**
   Hướng dẫn tự viết code kế thừa các lớp `VNFetcher`, `VNValidator`, `VNTransformer`, và `Exporter` để nhúng logic nghiệp vụ riêng (VD: làm giàu chỉ báo kỹ thuật) đồng bộ với Path Builder mới.
4. **[Luồng Dữ Liệu Thời Gian Thực](04-streaming.md)**
   *(Đặc quyền gói Golden/Diamond)* Hướng dẫn kiến trúc Streaming lấy dữ liệu thô, ghi vào Parquet/CSV với cấu trúc chuẩn hoá.
5. **[Vận Hành Bằng CLI & Các Use Case Thực Tế](05-cli-and-use-cases.md)**
   Hướng dẫn chi tiết quản trị kho dữ liệu (Storage, Catalog, Audit, Migration), xây dựng rổ thanh khoản tự động, và các kịch bản chạy định kỳ.
6. **[Tiêu Chuẩn Cấu Trúc Dữ Liệu Streaming](07-streaming-data-schemas.md)**
   Từ điển tham chiếu toàn bộ các trường dữ liệu trả về từ kết nối WebSocket.
7. **[Hướng Dẫn Nâng Cấp](08-migration-guide.md)**
   Hướng dẫn chuyển đổi cấu trúc lưu trữ và code cũ sang tiêu chuẩn mới nhất.

---

## 🔗 Liên Kết Hệ Sinh Thái

Để phát huy tối đa sức mạnh, hãy kết hợp `vnstock_pipeline` với các module khác:

- **vnstock_data**: [Tài liệu Data Wrapper](../vnstock-data/)
- **vnstock_ta**: [Tài liệu Phân Tích Kỹ Thuật](../vnstock_ta/)

---

*Phiên bản tài liệu: 3.x | Cập nhật: 06/2026*