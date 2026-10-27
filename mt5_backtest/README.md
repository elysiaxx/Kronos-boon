# Kronos × MetaTrader 5 — Backtest đo độ chính xác

Bộ công cụ này đo **độ chính xác dự báo** của mô hình Kronos trên dữ liệu
nến thật lấy từ tài khoản **MetaTrader 5 (demo hoặc real)**, theo phương pháp
**walk-forward** (trượt cửa sổ, không nhìn trước tương lai).

Mặc định cấu hình cho **XAUUSD khung M15**, nhưng đổi được sang bất kỳ
symbol / khung thời gian nào MT5 của bạn có.

> ⚠️ **Lưu ý quan trọng về môi trường**
> Thư viện Python `MetaTrader5` chỉ chạy trên **Windows** và cần một
> **terminal MT5 đang mở + đã đăng nhập** trên cùng máy. Vì vậy phần lấy dữ
> liệu trực tiếp từ MT5 phải chạy trên máy Windows (hoặc VPS Windows) của bạn.
> Nếu không có MT5 trên máy đang chạy, dùng chế độ `--source csv` với file đã
> export (xem bên dưới) — chế độ CSV chạy trên mọi hệ điều hành.

---

## 1. Cài đặt

Trên máy **Windows có MT5**:

```bash
# từ thư mục gốc repo Kronos
pip install -r requirements.txt                 # numpy, pandas, torch, matplotlib, huggingface_hub...
pip install -r mt5_backtest/requirements-mt5.txt # MetaTrader5 (chỉ Windows)
```

Trong terminal MT5: **Tools → Options → Expert Advisors**, bật *Allow Algo
Trading*; và kéo symbol **XAUUSD** vào *Market Watch* để MT5 tải lịch sử nến.

Lấy thông tin tài khoản demo từ broker (login dạng số, password, server).

---

## 2. Chạy backtest trực tiếp từ MT5

```bash
python -m mt5_backtest.backtest \
    --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --n_windows 50 --step 24 \
    --model NeoQuasar/Kronos-small \
    --mt5_login 1234567 --mt5_password "DEMO_PASS" --mt5_server "YourBroker-Demo"
```

Nếu terminal MT5 đã đăng nhập sẵn, có thể bỏ 3 tham số `--mt5_*` — script sẽ
dùng luôn phiên đang mở.

Ý nghĩa tham số chính:

| Tham số        | Mặc định | Ý nghĩa |
|----------------|----------|---------|
| `--lookback`   | 400      | Số nến lịch sử đưa vào mô hình (≤ context: 512 với small/base) |
| `--pred_len`   | 24       | Số nến dự báo mỗi cửa sổ (24 nến M15 = 6 giờ) |
| `--n_windows`  | 30       | Số cửa sổ walk-forward (giữ các cửa sổ gần nhất) |
| `--step`       | 24       | Bước trượt giữa 2 cửa sổ (`= pred_len` ⇒ không chồng lấn) |
| `--eval_horizon` | pred_len | Mốc nến dùng cho chỉ số hướng/chiến lược (1-based) |
| `--sample_count` | 1      | Số mẫu lấy trung bình mỗi cửa sổ (cao hơn ⇒ ổn định hơn) |
| `--T` / `--top_p` | 1.0 / 0.9 | Nhiệt độ & nucleus sampling (giảm T ⇒ xác định hơn) |
| `--seed`       | None     | Cố định ngẫu nhiên để lặp lại được |

---

## 3. Chạy bằng CSV (mọi hệ điều hành)

Trên máy Windows, export nến ra CSV một lần:

```bash
python -m mt5_backtest.export_mt5_csv --symbol XAUUSD --timeframe M15 --count 8000 --out xauusd_m15.csv
```

Rồi chạy backtest ở bất cứ đâu (không cần MT5):

```bash
python -m mt5_backtest.backtest --source csv --csv xauusd_m15.csv \
    --symbol XAUUSD --timeframe M15 --lookback 400 --pred_len 24 --n_windows 50
```

Chế độ CSV cũng nhận file xuất tay từ MT5 (các cột `time, open, high, low,
close, tick_volume, real_volume, spread` đều được tự nhận diện).

---

## 4. Kết quả

Script in ra bảng tóm tắt và lưu vào `mt5_backtest/results/`:

* `<symbol>_<tf>_<time>_report.json` — toàn bộ số liệu.
* `..._sample_window.png` — một cửa sổ mẫu: lịch sử + thực tế + dự báo.
* `..._dir_accuracy.png` — độ chính xác hướng theo từng mốc dự báo.
* `..._equity_curve.png` — đường vốn của chiến lược "theo hướng" đơn giản.

Các chỉ số:

* **MAE / RMSE / MAPE** — sai số của đường giá `close` dự báo so với thực tế.
* **Directional accuracy** — tỉ lệ mô hình đoán đúng **hướng** (lên/xuống) so
  với giá đóng cửa cuối cùng quan sát được. **50% = tung đồng xu**; cao hơn
  đáng kể và ổn định mới có giá trị.
* **Naive strategy** — long/short thuần theo hướng dự báo tại `eval_horizon`,
  quy ra % lợi nhuận để biết dự báo có *dùng được* không. Đây **không phải
  khuyến nghị đầu tư**, chỉ là một thước đo.

---

## 5. Lưu ý & giới hạn

* **Kronos sinh mẫu ngẫu nhiên.** Chạy 2 lần có thể lệch nhau. Dùng `--seed`
  để lặp lại, và tăng `--sample_count` (vd 5–10) để kết quả ổn định hơn.
* **Không có nhìn trước (look-ahead).** Mỗi dự báo chỉ thấy nến *trước* đoạn
  được chấm điểm; phần tương lai được giữ kín để so sánh.
* **Múi giờ.** Thời gian nến lấy theo giờ server của MT5. Kronos chỉ dùng đặc
  trưng chu kỳ (phút/giờ/thứ/ngày/tháng) nên không ảnh hưởng miễn là nhất quán.
* **Volume.** XAUUSD/forex thường không có volume thật (`real_volume = 0`) nên
  dùng `tick_volume`; `amount` để Kronos tự suy ra như mặc định thư viện.
* **Bước tiếp theo (tùy chọn).** Sau khi đo độ chính xác, có thể dựng vòng
  *forward-test* đặt lệnh trên tài khoản demo (`order_send`) để kiểm tra trong
  điều kiện thật — hỏi để mình bổ sung module đó.
```
