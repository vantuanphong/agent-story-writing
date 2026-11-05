# Hướng dẫn cài đặt (cho máy mới)

Bộ mã này gồm 2 công cụ:
- **Viết truyện dài** từ 1 file tóm tắt (chia chương, tự soát lỗi, viết tiếp được).
- **Phòng họp biên kịch** — nhiều vai thảo luận ra kịch bản ngắn.

Tất cả chạy bằng model trên máy bạn (Ollama). Không tốn tiền API, không cần mạng sau khi tải model.

---

## 0. Máy cần gì

| Mục | Tối thiểu | Nên có |
|---|---|---|
| Hệ điều hành | Windows 10/11, Linux, macOS | — |
| RAM | 16 GB | 16 GB+ |
| VRAM (GPU) | 6 GB | 8 GB+ (chạy model 7B mượt) |
| Ổ cứng | 12 GB trống | 20 GB |
| Python | 3.11 | 3.11 (đừng dùng 3.13 — thư viện chưa chắc tương thích) |

Không có GPU vẫn chạy được, chỉ chậm hơn khoảng 5–10 lần.

---

## 1. Cài Python 3.11

Tải tại https://www.python.org/downloads/release/python-3119/

Khi cài, **tích ô "Add python.exe to PATH"** (bắt buộc).

Kiểm tra:

```powershell
python --version
```

Phải ra `Python 3.11.x`. Nếu báo "không tìm thấy", cài lại và nhớ tích PATH.

---

## 2. Cài Ollama

Tải tại https://ollama.com/download → cài → mở ứng dụng (biểu tượng con lạc đà ở khay hệ thống).

Kiểm tra:

```powershell
ollama --version
curl http://127.0.0.1:11434
```

Dòng `Ollama is running` là đúng.

---

## 3. Tải model

Bộ cấu hình mặc định dùng 2 model:

```powershell
ollama pull qwen2.5:7b-instruct
ollama pull deepseek-r1:8b
```

Dung lượng khoảng 5 GB + 5 GB. Tùy mạng, có thể mất 10–30 phút.

**Nếu tải `qwen2.5:7b-instruct` không được** (hoặc máy yếu), dùng bản nhỏ hơn:

```powershell
ollama pull qwen2.5:3b
```

rồi mở `config.toml`, sửa dòng `model = ...` trong `[models.nhanh]` thành `qwen2.5:3b`.

Kiểm tra đã tải đúng:

```powershell
ollama list
```

---

## 4. Cài thư viện Python

Mở PowerShell **tại thư mục vừa giải nén** (mẹo: mở thư mục, bấm vào thanh địa chỉ, gõ `powershell`, Enter).

```powershell
python -m venv venv_autogen
.\venv_autogen\Scripts\python.exe -m pip install --upgrade pip
.\venv_autogen\Scripts\python.exe -m pip install -r requirements.txt
```

> Nếu pip báo "dependency resolver" hay xung đột phiên bản cứ bỏ qua — bộ này đã chạy thật với đúng `pyautogen 0.2.35` + `openai 3.41.0`.

Trên Linux/macOS thì đường dẫn là `./venv_autogen/bin/python`.

---

## 5. Kiểm tra trước khi chạy

```powershell
.\venv_autogen\Scripts\python.exe selfcheck.py
.\venv_autogen\Scripts\python.exe check.py
.\venv_autogen\Scripts\python.exe check_ollama.py
```

Kết quả mong đợi:

```
selfcheck : 19/19 PASS
check     : ✓ Cấu hình hợp lệ, model đã sẵn sàng
check_ollama: KẾT LUẬN: OK - tất cả model trong config đều sẵn sàng
```

Nếu `check.py` báo `FAIL ... chưa tải` → quay lại bước 3.
Nếu `check_ollama.py` báo không gọi được Ollama → mở app Ollama.

---

## 6. Chạy thử (5 chương ngắn, ~3 phút)

```powershell
.\venv_autogen\Scripts\python.exe script_viet_truyen.py --chapters 5 --words 600
```

Không truyền `--input` thì nó dùng file mẫu `vi_du_dau_vao.md` (truyện trinh thám ở thị trấn ven biển).

Kết quả nằm trong `output\`:

| File | Nội dung |
|---|---|
| `truyen_*.md` | truyện — mở bằng Notepad là đọc được |
| `*_ke_hoach.md` | bible nhân vật + dàn ý |
| `*.json` | sổ trạng thái, dùng để viết tiếp |

---

## 7. Viết truyện của bạn

Tạo file `TenTruyen.md` gồm đúng 4 mục (xem `vi_du_dau_vao.md` làm mẫu):

```
Plot
<tóm tắt cốt truyện, 3–5 đoạn>

Setting
<bối cảnh, thời đại, không khí>

Style
<giọng văn, ngôi kể>

Characters
Tên nhân vật
<tính cách, ngoại hình, điều đang giấu>
```

Chạy:

```powershell
.\venv_autogen\Scripts\python.exe script_viet_truyen.py --input TenTruyen.md --chapters 12 --words 900
```

Muốn xem dàn ý trước, chưa viết:

```powershell
.\venv_autogen\Scripts\python.exe script_viet_truyen.py --input TenTruyen.md --chapters 12 --plan-only
```

Bị dừng giữa chừng (Ctrl+C, mất điện, lỗi): chạy lại

```powershell
.\venv_autogen\Scripts\python.exe script_viet_truyen.py --resume
```

---

## 8. Đổi model / giọng văn

Mở `config.toml`:

```toml
[models.nhanh]
model = "qwen2.5:7b-instruct"     # đổi tên model ở đây
num_ctx = 16384                    # giảm nếu máy thiếu VRAM
```

Mở `prompts/chapter.md`, sửa phần sau `@@SYSTEM@@` để đổi giọng văn.

Sau khi sửa, chạy `check.py` để chắc không gõ sai.

---

## 9. Phòng họp biên kịch (kịch bản ngắn)

```powershell
.\venv_autogen\Scripts\python.exe script_phong_hop.py --input vi_du_dau_vao.md
```

Ra `output\kich_ban_<giờ>.txt`. Cái này ra kịch bản ngắn (~5 KB), **không dùng để viết truyện dài**.

---

## 10. Dựng prompt video (tuỳ chọn)

Biến truyện thành prompt cho MiniMax H3 / ComfyUI:

```powershell
.\venv_autogen\Scripts\python.exe script_prompt_minimax.py --input output\truyen_....md --limit 3
```

`--limit 3` để thử 3 clip trước. Kết quả: `output\video_<giờ>\seg_001.txt`, `seg_002.txt`…
mỗi file là một lần Generate. Xem `contact.md` để biết clip nào còn lỗi.

Cấu hình trong `config.toml` mục `[pipeline.video]`: độ dài clip (10s = 243 frames),
fps, số shot tối đa, và bảng `[pipeline.video.references]` (nhân vật ↔ `<Picture N>`).

---

## 11. Lỗi thường gặp

| Lỗi | Nguyên nhân | Cách xử |
|---|---|---|
| `python : not recognized` | Chưa cài Python hoặc chưa tích PATH | Cài lại Python 3.11, tích "Add to PATH" |
| `Activate.ps1 cannot be loaded` | PowerShell chặn script | Đừng activate; gọi thẳng `.\venv_autogen\Scripts\python.exe` |
| `Model '...' chưa tải` | Thiếu model | `ollama pull <tên model>` |
| `Không gọi được Ollama` | App Ollama chưa chạy | Mở Ollama, đợi biểu tượng hiện |
| `request exceeds context size` | Chương quá dài so với `num_ctx` | Giảm `--words`, hoặc tăng `num_ctx` |
| Chạy rất chậm | Không có GPU / VRAM thấp | Đổi model nhỏ hơn (`qwen2.5:3b`), giảm `--words` |
| Truyện có chữ Hán | Model lạc ngôn ngữ | Tự viết lại chương; lặp nhiều thì đổi model |
| `Khong the ket noi...` khi pip | Mạng/proxy | Thử lại, hoặc dùng `pip install --proxy ...` |

---

## 12. Cấu trúc thư mục

```
config.toml            cấu hình: model, vai, số chương
prompts/               7 file lời nhắc (sửa bằng Notepad)
truyen/                thư viện dùng chung
script_viet_truyen.py  viết truyện dài
script_phong_hop.py    phòng họp biên kịch
selfcheck.py           tự kiểm tra (không cần model)
check.py               kiểm config + model
check_ollama.py        ping Ollama + gọi thử
vi_du_dau_vao.md       file đầu vào mẫu
requirements.txt       thư viện cần cài
```

Chi tiết sâu hơn: xem `README.md`.

---

## 13. Mẹo dùng thật

- Mỗi chương 900–1200 từ là điểm ngọt. Đẩy lên 2000 từ model dễ lặp.
- 12 chương ≈ 10.000 từ ≈ 10 phút máy. 30 chương ≈ 30 phút.
- Đừng mở 2 cửa sổ chạy cùng một truyện — hai tiến trình ghi đè file trạng thái của nhau.
- `Ctrl+C` lúc nào cũng an toàn: chương đang viết mất, chương đã xong còn nguyên.
- Truyện ra là **bản nháp tốt**, không phải bản in. Đọc lại và sửa tay phần hội thoại.
