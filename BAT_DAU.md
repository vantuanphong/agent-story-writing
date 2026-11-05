# BẮT ĐẦU TỪ ĐÂY

Bộ này viết **truyện dài** bằng model chạy trên máy bạn. Không tốn tiền API.

## 5 bước

**1.** Cài **Python 3.11** (tích ô *Add python.exe to PATH*) và **Ollama** (ollama.com/download).

**2.** Mở PowerShell, tải model:

```powershell
ollama pull qwen2.5:7b
ollama pull deepseek-r1:8b
```

**3.** Tại thư mục vừa giải nén:

```powershell
python -m venv venv_autogen
.\venv_autogen\Scripts\python.exe -m pip install -r requirements.txt
```

**4.** Kiểm tra:

```powershell
.\venv_autogen\Scripts\python.exe selfcheck.py
```

Thấy `19/19 PASS` là đúng.

**5.** Chạy thử (dùng truyện mẫu, ~3 phút):

```powershell
.\venv_autogen\Scripts\python.exe script_viet_truyen.py --chapters 5 --words 600
```

Truyện ra trong `output\truyen_*.md` — mở bằng Notepad là đọc được.

---

## Viết truyện của bạn

Tạo file `TenTruyen.md` gồm 4 mục `Plot` / `Setting` / `Style` / `Characters`
(xem `vi_du_dau_vao.md`), rồi:

```powershell
.\venv_autogen\Scripts\python.exe script_viet_truyen.py --input TenTruyen.md --chapters 12 --words 900
```

Dừng giữa chừng (Ctrl+C, mất điện, lỗi) → chạy tiếp bằng `--resume`.

Muốn dựng **prompt video** từ truyện (MiniMax H3 / ComfyUI):

```powershell
.\venv_autogen\Scripts\python.exe script_prompt_minimax.py --input output\truyen_....md --limit 3
```

---

## Khi gặp lỗi

| Lỗi | Xử lý |
|---|---|
| `python : not recognized` | Cài lại Python 3.11, tích "Add to PATH" |
| `Model '...' chưa tải` | `ollama pull <tên model>` |
| `Không gọi được Ollama` | Mở app Ollama |
| Chạy chậm | Máy không có GPU: chấp nhận, hoặc dùng model nhỏ hơn |

Chi tiết đầy đủ: **`HUONG_DAN_CAI_DAT.md`** (cài đặt, 12 mục)
Tra cứu lệnh + đổi model: **`README.md`**
