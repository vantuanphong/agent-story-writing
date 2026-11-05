# Viết truyện dài & phòng họp biên kịch (model local)

Hai công cụ, dùng chung một `config.toml`:

- **Pipeline truyện** — từ 1 file tóm tắt ra truyện nhiều chương (chia chương, tự soát lỗi, nén trí nhớ, viết tiếp được).
- **Phòng họp biên kịch** — nhiều vai thảo luận ra kịch bản (chạy trên Ollama hoặc API trả tiền).

Không cần cài thêm gì ngoài `venv_autogen` đã có.

---

## 1. Ba lệnh đầu tiên

```powershell
cd C:\Users\vantu_arqecln\Desktop\Project\Truyen\AutoGen
$py = ".\venv_autogen\Scripts\python.exe"

& $py selfcheck.py         # kiểm code + config, không cần model (~1s)
& $py check.py             # kiểm config + model đã tải
& $py check_ollama.py      # ping Ollama + gọi thử từng model
```

Cả ba phải xanh trước khi chạy thật.

---

## 2. Chạy

| Việc | Lệnh |
|---|---|
| Viết truyện mới | `viet_truyen.bat` hoặc `& $py script_viet_truyen.py` |
| Viết 24 chương × 1200 từ | `& $py script_viet_truyen.py --chapters 24 --words 1200` |
| Xem dàn ý trước, chưa viết | `& $py script_viet_truyen.py --plan-only` |
| Viết tiếp lần dở | `& $py script_viet_truyen.py --resume` |
| Chọn đúng file trạng thái | `--resume-file output\truyen_....json` |
| Phòng họp biên kịch | `run_ollama.bat` hoặc `& $py script_phong_hop.py` |
| Dùng bộ cấu hình khác | thêm `--config ten_khac.toml` |

`Ctrl+C` bất cứ lúc nào: chương đang viết dở mất, các chương đã xong còn nguyên.

---

## 3. Muốn đổi gì → sửa ở đâu

| Muốn | Sửa |
|---|---|
| Đổi model của một chặng | `config.toml` → `[pipeline.truyen.<chặng>] model = "<tên model trong [models]>"` |
| Đổi giọng văn, luật viết | `prompts/chapter.md` (và các file khác trong `prompts/`) |
| Đổi nhân vật/rule cho 1 vai | `config.toml` → `[roles.<vai>] system = "..."` hoặc trỏ file `.md` |
| Thêm vai vào phòng họp | thêm `[roles.ten_vai]` + thêm tên vào `[groupchat] members` |
| Đổi số chương / số từ | `config.toml` → `[pipeline.truyen] chapters`, `words` |
| Dùng API trả tiền thay Ollama | `[server] kind = "openai"`, đặt biến môi trường của model |
| Tăng context cho chương dài | `config.toml` → `num_ctx` của model, hoặc `--ctx 24576` |
| Tắt tầng soát lỗi | `--no-review` hoặc `[pipeline.truyen] review_enabled = false` |
| Đổi model khi đang viết dở | `--resume --model <tên model khác>` |

**Không cần sửa file `.py` nào** cho các việc trên.

---

## 4. Cấu trúc

```
config.toml                 cấu hình duy nhất (server, model, vai, chặng)
prompts/                    lời nhắc cho model, sửa bằng Notepad
  bible.md style.md outline.md chapter.md continue.md review.md compact.md
truyen/                     thư viện dùng chung
  config.py                 đọc + kiểm config
  client.py                 gọi Ollama (/api/chat, đặt được num_ctx)
  progress.py               in tiến trình, nhịp tim
  audit.py                  soát văn: chữ Hán, nhãn rác, đại từ, câu lặp, phình
  context.py                dựng "gói bàn giao" cho mỗi chương
  state.py                  lưu 3 file, ghi atomic, tìm lần chạy dở
  groupchat.py              phòng họp biên kịch
script_viet_truyen.py       chạy pipeline truyện
script_phong_hop.py         chạy phòng họp
script_autogen.py           alias của script_phong_hop.py (giữ thói quen cũ)
script_autogen_ollama.py    alias của script_phong_hop.py
check.py  check_ollama.py  selfcheck.py
run_ollama.bat  viet_truyen.bat  vao_venv.bat
output/                     truyện, kế hoạch, trạng thái, kịch bản
```

### Định dạng file prompt

```
@@SYSTEM@@
Bạn là nhà văn Việt Nam...
@@USER@@
### BIBLE
{bible}
...
```

`{bible}`, `{words}`, `{tail}`… là biến máy tự điền. Gõ sai tên biến thì chữ `{tên_sai}`
được giữ nguyên trong prompt (để bạn thấy), không làm sập chương trình.

---

## 5. Sự cố thường gặp (đều đã gặp thật)

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `Model '...' chưa tải` | Chưa `ollama pull` | `ollama pull <tên>`, sửa `[models]` |
| `400 Cannot have 2 or more assistant messages` | Ollama không nhận 2 message cùng role | Đã vá tự động; nếu còn, kiểm `[server] kind` |
| `request (...) exceeds context size` | Chương quá dài so với `num_ctx` | Tăng `num_ctx` / `--ctx`, hoặc giảm `--words` |
| `⚠ Chương bị phình (N từ) - cắt về M từ` | Model lặp vô hạn | Bình thường, đã tự cắt |
| Chương có chữ Hán | Model lạc ngôn ngữ | Tự viết lại; lặp nhiều thì đổi model |
| `Activate.ps1 cannot be loaded` | PowerShell chặn script | Dùng `.bat`, hoặc `powershell -ExecutionPolicy Bypass` |
| Treo im lặng lâu | Model chậm / sinh quá dài | Có heartbeat 15s; mỗi lượt timeout 900s |
| Kịch bản ngắn (~5 KB) | Bản cũ dồn cả truyện vào 1 tin nhắn | Dùng `script_viet_truyen.py`, không dùng phòng họp |

---

## 6. Nguyên tắc hoạt động

```
1 file tóm tắt
   ↓
① bible        chốt nhân vật, giới tính, xưng hô, luật phép thuật, bí mật
   ↓
② mẫu giọng    1 đoạn văn thật, nhét vào mọi chương sau
   ↓
③ dàn ý        N chương: mục tiêu | sự kiện | hook  (tự kiểm trùng)
   ↓
④ vòng chương:  prompt → model viết → cắt phình → máy soát lỗi → ghi đĩa ngay
   ↓            (mỗi 8 chương: nén trí nhớ thành recap + trạng thái)
⑤ ra 3 file    truyen.md · ke_hoach.md · state.json
```

Điểm mấu chốt: **không bao giờ gửi cả truyện vào prompt**. Mỗi chương chỉ nhận
`bible + mẫu giọng + dàn ý quanh chương đó + bản nén + đuôi chương trước`.

---

## 7. Ghi chú

- Không cần internet, trừ khi `[server] kind = "openai"`.
- Mọi con số trong code (chặn phình 2,2×, 6 bộ soát lỗi, nén mỗi 8 chương) đều là tham số đã đo trên máy này, sửa được nếu cần.
