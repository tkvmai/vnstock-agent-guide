# Vnstock Pipeline - Vận Hành Bằng CLI & Các Use Case Thực Tế

## Giới Thiệu

`vnstock_pipeline` từ phiên bản 2.3.0 cung cấp bộ công cụ Giao Diện Dòng Lệnh (CLI) vô cùng mạnh mẽ giúp bạn quản trị toàn bộ Kho dữ liệu (Storage), khởi chạy các luồng Pipeline tự động, và kiểm tra chất lượng dữ liệu mà không cần phải viết bất kỳ dòng code Python nào.

Đây là cách nhanh nhất và thực tế nhất để vận hành hệ thống trong môi trường Production (như VPS, cron jobs).

---

## I. Quản Trị Kho Dữ Liệu Tập Trung

Thay vì phải sửa code, bạn có thể định hình cấu trúc toàn bộ kho dữ liệu thông qua cấu hình `pipeline.toml` bằng dòng lệnh.

### 1. Xem và Thay Đổi Cấu Hình Cơ Bản

```bash
# Xem thông số cấu hình kho dữ liệu hiện tại
python -m vnstock_pipeline.cli storage config

# Khởi tạo file cấu hình mặc định (nếu chưa có)
python -m vnstock_pipeline.cli storage init-config

# Khởi tạo thư mục rỗng (Scaffold) theo cấu hình hiện tại
python -m vnstock_pipeline.cli storage init --category ohlcv --domain market --layer raw

# Thay đổi định dạng lưu trữ mặc định sang CSV (dễ mở bằng Excel, dung lượng lớn)
python -m vnstock_pipeline.cli storage set-format csv

# Chuyển đổi định dạng sang Parquet (Tối ưu dung lượng & tốc độ đọc cho AI)
# Khi chạy lệnh này, hệ thống sẽ tự động quét và hỏi bạn có muốn Migrate (chuyển đổi) file cũ hay không!
python -m vnstock_pipeline.cli storage set-format parquet -y

# Đổi kiến trúc thư mục lưu trữ (flat cho cá nhân, nested cho hệ thống phân tán Data Lake)
python -m vnstock_pipeline.cli storage set-layout nested
```

### 2. Dọn Dẹp, Báo Cáo & Kiểm Soát Chất Lượng

```bash
# Hiển thị cấu trúc cây dữ liệu đang có
python -m vnstock_pipeline.cli storage tree -L 3

# Báo cáo thống kê CSDL (Tổng file, dung lượng)
python -m vnstock_pipeline.cli storage report

# Xoá tự động các file dữ liệu cũ hơn 90 ngày để giải phóng ổ cứng VPS
python -m vnstock_pipeline.cli storage cleanup --days 90

# Phân tích chất lượng dữ liệu: phát hiện đứt gãy thời gian (gaps) và file lỗi, quá hạn
python -m vnstock_pipeline.cli storage audit --category ohlcv --stale-days 5

# Quét và tái tạo lại Metadata Catalog (File JSON lưu thống kê size/rows của CSDL)
python -m vnstock_pipeline.cli storage build-catalog
```

### 3. Quản Lý & Bảo Vệ Cấu Trúc Dữ Liệu

Khi dữ liệu mới có sự cố lệch Schema, nó sẽ bị đưa vào khu vực Quarantine.
```bash
# Xem cấu trúc Schema chuẩn đang áp dụng cho một category
python -m vnstock_pipeline.cli schema show ohlcv

# Xoá cấu trúc chuẩn để hệ thống tự động ghi nhận lại ở lần tải tới
python -m vnstock_pipeline.cli schema reset ohlcv

# Xem danh sách các file dữ liệu bị cách ly do lệch cấu trúc
python -m vnstock_pipeline.cli quarantine list

# Cập nhật schema chuẩn dựa trên file đang bị cách ly (chấp nhận cấu trúc mới)
python -m vnstock_pipeline.cli quarantine adopt ohlcv ACB <timestamp>
```

### 3. Di Chuyển Dữ Liệu Cũ

Nếu bạn có dữ liệu từ phiên bản vnstock_pipeline cũ (v2.2.1 trở xuống) nằm rải rác:

```bash
# Di chuyển dữ liệu vào Config Hub tự động và tự sắp xếp đúng layout mới
python -m vnstock_pipeline.cli storage migrate-legacy --source ./old_data_folder
```

---

## II. Các Ứng Dụng Thực Tế

Hệ thống có cơ chế tự động ghi nhớ các trạng thái nên bạn có thể chạy lặp lại các lệnh này hằng ngày bằng cron job mà không sợ trùng lặp dữ liệu.

### 1. Tự Động Xây Dựng Rổ Thanh Khoản

Thay vì phải tự lên danh sách mã để kéo dữ liệu, bạn có thể dùng CLI tự động quét và chọn lọc ra Top 300 mã có dòng tiền tốt nhất (Đa khung thời gian).

```bash
# Xây dựng rổ mã thanh khoản và lưu tự động vào 'pipeline.toml' với tên 'liquidity_auto'
python -m vnstock_pipeline.cli universe build-liquidity --days 50 --top 300
```

### 2. Khởi Tạo & Cập Nhật Giá Cổ Phiếu

**Khởi tạo dữ liệu dài hạn với cơ chế Fallback nguồn tự động:**
Lệnh này đặc biệt hữu dụng khi lấy dữ liệu lịch sử rất dài (Long Init). Do một số nguồn có thể giới hạn hoặc thiếu dữ liệu ở quá khứ xa, bạn có thể truyền tham số `--sources` để khai báo nhiều nguồn. Hệ thống sẽ ưu tiên nguồn đầu tiên, nếu bị lỗi kết nối hoặc rỗng sẽ tự động dự phòng (fallback) sang nguồn tiếp theo.

```bash
python -m vnstock_pipeline.cli run ohlcv --tickers ACB,VCB --sources VCI,KBS,VND
```

**Cập nhật dữ liệu hàng loạt theo Sàn (Exchange) hoặc Nhóm (Group):**
Hệ thống hỗ trợ lấy toàn bộ cổ phiếu trên một sàn (`HOSE`, `HNX`, `UPCOM`) hoặc các rổ chỉ số (`VN30`, `VN100`) tự động thông qua cờ `--group`.

```bash
# Lấy toàn bộ OHLCV của HOSE hằng ngày
python -m vnstock_pipeline.cli run ohlcv --group HOSE --mode daily

# Lấy dữ liệu cho rổ thanh khoản vừa quét ở trên
python -m vnstock_pipeline.cli run ohlcv --watchlist liquidity_auto --mode daily

# Nếu có mã bị lỗi (đứt mạng), chạy lại lệnh với cờ --retry-errors để chỉ cày lại mã xịt! (File log tự động được đọc từ Config Hub)
python -m vnstock_pipeline.cli run ohlcv --retry-errors
```

> [!TIP]
> Để tự động hóa luồng tải dữ liệu lớn, hãy xem kịch bản chuyên nghiệp được định nghĩa sẵn trong `job_examples/sync_market_data.py`. Kịch bản này sử dụng `sys.executable` (hỗ trợ Virtual Environment tuỳ chỉnh) cùng với cơ chế bắt lỗi an toàn (catch error per task) giúp bạn tải trọn vẹn toàn bộ các nhóm dữ liệu mà không lo bị treo tiến trình (crash).

### 3. Thu Thập Báo Cáo Tài Chính Hàng Quý

```bash
# Quét báo cáo tài chính mới nhất cho nhóm VN30
python -m vnstock_pipeline.cli run financial --watchlist vn30
```

### 4. Thu Thập Tin Tức Tự Động

Hệ thống sử dụng Streaming Buffer để lấy báo chí không giới hạn từ CafeF, VNExpress,...

```bash
# Chạy hằng ngày cuối phiên để lấy bài báo mới
python -m vnstock_pipeline.cli run news --sources all

# Chạy dạng Live (lặp lại mỗi 5 phút)
python -m vnstock_pipeline.cli run news --mode live --interval 300
```

### 5. Xuất Dữ Liệu Cho Phần Mềm Amibroker

Nếu bạn đang dùng Amibroker để soi Chart, bạn có thể trích xuất Database hiện tại ra chuẩn cho Amibroker cực nhanh:

```bash
# Chạy lệnh không cần flag --date để hệ thống tự động lấy ngày hiện tại (phù hợp cho Cronjob cập nhật EOD)
python -m vnstock_pipeline.cli export amibroker --output ./AmibrokerData
```

---

## III. Công Cụ Kiểm Tra Dữ Liệu Trực Tiếp

Khi làm việc trên Server Linux/VPS không có giao diện đồ họa, các lệnh sau giúp bạn "xem" bên trong các file Parquet/CSV nhanh chóng:

1. **Xem Cấu Trúc Tổng Quan**
   Hiển thị ngẫu nhiên các file đại diện của CSDL hiện tại.

   ```bash
   python -m vnstock_pipeline.cli storage preview
   python -m vnstock_pipeline.cli storage preview --category ohlcv
   python -m vnstock_pipeline.cli storage preview --symbol ACB
   ```
1. **Xem Chi Tiết 1 File Bất Kỳ**

   ```bash
   # Xem số dòng/cột, schema và 5 dòng dữ liệu mẫu đầu tiên của file Parquet
   python -m vnstock_pipeline.cli inspect ~/vnstock_db/ohlcv/ACB.parquet

   # Xem 20 dòng đầu hoặc cuối
   python -m vnstock_pipeline.cli head ~/vnstock_db/ohlcv/ACB.csv --rows 20
   ```
1. **Truy Vấn Theo Điều Kiện Dạng SQL**
   Lọc/truy vấn trực tiếp file dữ liệu siêu tốc bằng câu lệnh SQL:

   ```bash
   python -m vnstock_pipeline.cli query ~/vnstock_db/ohlcv/ACB.parquet "close > 50 AND volume > 1000000"
   ```
1. **Thống Kê Mô Tả**
   Liệt kê Count, Mean, Min, Max và tỷ lệ trống dữ liệu (NaN) của từng cột.

   ```bash
   python -m vnstock_pipeline.cli stats ~/vnstock_db/ohlcv/ACB.parquet
   ```

> [!TIP]
>
> **Triển khai Cron Job**
> Lên lịch chạy các lệnh `vnstock_pipeline.cli run ...` bằng Crontab (Linux) hoặc Task Scheduler (Windows) vào các khung giờ vàng (như 15h30 chiều cho Giá và 18h00 cho Tin Tức/Thống kê giao dịch) để tự động hóa hoàn toàn luồng thu thập.