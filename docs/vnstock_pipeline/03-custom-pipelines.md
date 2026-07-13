# Vnstock Pipeline - Xây Dựng Quy Trình Tùy Chỉnh

## Giới Thiệu

Chương này hướng dẫn xây dựng **quy trình tùy chỉnh** - các quy trình riêng biệt để giải quyết các bài toán thực tế như:
- Tự động lấy dữ liệu từ endpoint API tùy chỉnh.
- Làm giàu dữ liệu bằng các chỉ báo kỹ thuật trước khi lưu.
- Khởi tạo Exporter của riêng bạn (ví dụ: đẩy dữ liệu ra Webhook) thay vì lưu file.

---

## I. Kiến Trúc Tùy Chỉnh

### Luồng Xử Lý

```text
Dữ Liệu Đầu Vào (Danh sách mã) 
    ↓
Fetcher (Thu thập dữ liệu)
    ↓
Validator (Xác thực chất lượng / Lọc dữ liệu)
    ↓
Transformer (Tính toán thêm chỉ báo)
    ↓
Exporter (Lưu vào thư mục cấu hình sẵn hoặc gọi Webhook)
```

---

## II. Các Mẫu Thiết Kế Tùy Chỉnh

### 1. Trình Thu Thập Tùy Chỉnh

**Mục đích**: Thu thập dữ liệu từ endpoint API nội bộ của bạn.

```python
from vnstock_pipeline.template.vnstock import VNFetcher
import pandas as pd
import requests

class APIFetcher(VNFetcher):
    """Thu thập dữ liệu từ endpoint API tùy chỉnh"""
    
    def __init__(self, api_url: str):
        self.api_url = api_url
    
    def _vn_call(self, ticker: str, **kwargs) -> pd.DataFrame:
        params = {
            "symbol": ticker,
            "from": kwargs.get("start", "2024-01-01")
        }
        try:
            response = requests.get(self.api_url, params=params, timeout=10)
            response.raise_for_status()
            df = pd.DataFrame(response.json()['quotes'])
            return df
        except Exception as e:
            print(f"Lỗi khi thu thập {ticker}: {e}")
            return pd.DataFrame()
```

### 2. Trình Xác Thực Tùy Chỉnh

**Mục đích**: Chặn (Không lưu) các file dữ liệu vi phạm logic nghiệp vụ.

```python
from vnstock_pipeline.template.vnstock import VNValidator
import pandas as pd

class BusinessValidator(VNValidator):
    def validate(self, data: pd.DataFrame) -> bool:
        if not super().validate(data):
            return False
            
        # Logic tùy chỉnh: Chỉ lưu những mã có khối lượng > 0
        if 'volume' in data.columns and (data['volume'] <= 0).any():
            print("❌ Lỗi: Khối lượng giao dịch không hợp lệ (<= 0)")
            return False
            
        return True
```

### 3. Trình Biến Đổi Tùy Chỉnh (Tính Toán Chỉ Báo Kỹ Thuật)

**Mục đích**: Tính toán và bổ sung chỉ báo kỹ thuật vào dữ liệu giá OHLCV.

```python
from vnstock_pipeline.template.vnstock import VNTransformer
from vnstock_ta import Indicator
import pandas as pd
import numpy as np

class TAEnrichedTransformer(VNTransformer):
    """Làm giàu dữ liệu bằng các chỉ báo kỹ thuật"""
    
    def transform(self, data: pd.DataFrame) -> pd.DataFrame:
        df = super().transform(data)
        
        # Khởi tạo bộ chỉ báo kỹ thuật
        ta = Indicator(data=df)
        
        # Thêm Moving Averages
        df['sma20'] = ta.trend.sma(length=20)
        df['sma50'] = ta.trend.sma(length=50)
        
        # Thêm RSI
        df['rsi'] = ta.momentum.rsi(length=14)
        
        # Thêm Volatility
        pct_change_df = pd.DataFrame({'close': df['close'].pct_change() * 100})
        ta_pct = Indicator(data=pct_change_df)
        df['volatility_30d'] = ta_pct.volatility.stdev(length=30)
        
        return df
```

### 4. Trình Xuất Dữ Liệu Tùy Chỉnh (Đẩy dữ liệu qua Webhook)

**Mục đích**: Thay vì lưu vào ổ cứng, đẩy dữ liệu lên hệ thống khác.

```python
from vnstock_pipeline.core.exporter import Exporter
import requests
from datetime import datetime

class WebhookExporter(Exporter):
    """Đẩy dữ liệu trực tiếp lên API Server"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def export(self, data, ticker: str, **kwargs):
        payload = {
            "ticker": ticker,
            "timestamp": datetime.now().isoformat(),
            "count": len(data),
            "data": data.to_dict('records')
        }
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            if response.status_code == 200:
                print(f"✅ {ticker}: Đã gửi thành công!")
        except Exception as e:
            print(f"❌ {ticker}: Lỗi Webhook - {e}")
            
    def preview(self, ticker: str, n: int = 5, **kwargs):
        return None
```

---

## III. Tích hợp Quy Trình Tùy Chỉnh với Cấu Hình Tập Trung

Từ v3.0, nếu bạn sử dụng Exporter dựng sẵn (VD: `ParquetExport`), bạn không cần (và không nên) tự quản lý `base_path`. Hãy để hệ thống tự động nhận diện từ `pipeline.toml`.

```python
from vnstock_pipeline.core.scheduler import Scheduler
from vnstock_pipeline.core.exporter import ParquetExport

# Khởi tạo Exporter. Hệ thống sẽ tự xác định base_path và mode layout từ file cấu hình ~/.vnstock/config/pipeline.toml
# Bạn chỉ cần đặt tên category cho logic của mình.
exporter = ParquetExport(data_type="custom_ta_data")

scheduler = Scheduler(
    fetcher=APIFetcher(api_url="http://my-api/quotes"),
    validator=BusinessValidator(),
    transformer=TAEnrichedTransformer(),
    exporter=exporter,
    max_workers=3
)

# Chạy
scheduler.run(['ACB', 'VCB'])
```

Kết quả sẽ tự động lưu vào cấu trúc chuẩn, ví dụ: `~/vnstock_db/custom_ta_data/ACB.parquet` (ở chế độ flat layout) và được `MetadataManager` quản lý số lượng dòng!
