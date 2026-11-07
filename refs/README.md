# Ảnh tham chiếu nhân vật (R2V)

Script **không đọc** ảnh trong thư mục này. Nó chỉ in ra thẻ `<Picture N>`
trong prompt; ảnh thật do bạn nạp vào node Reference của ComfyUI.

## Quy tắc khớp

| Trong `config.toml` | Nạp vào ComfyUI |
|---|---|
| `1 = { name = "Hải", image = "refs/hai.png" }` | ảnh thứ **1** trong node Reference |
| `2 = { name = "Lam", image = "refs/lam.png" }` | ảnh thứ **2** |
| `3 = { name = "Ông Tư", image = "refs/ong_tu.png" }` | ảnh thứ **3** |

- Số thứ tự phải **trùng** với thứ tự ảnh bạn thêm vào ComfyUI. Lệch một cái là
  nhân vật này đội mặt nhân vật kia.
- `name` phải khớp tên trong mục `Characters` của truyện. Không dấu vẫn khớp
  ("Hai" = "Hải").
- Ảnh nên là: chân dung rõ mặt, nền trơn, ánh sáng trung tính, 1 người.

## Chuẩn bị ảnh

1. Mỗi nhân vật 1 ảnh chân dung, kích thước tối thiểu 768×768.
2. Đặt tên file không dấu, không khoảng trắng: `hai.png`, `lam.png`, `ong_tu.png`.
3. Để vào thư mục này, rồi sửa đường dẫn trong `config.toml` nếu khác.

Thiếu ảnh của nhân vật nào thì script sẽ cảnh báo và mô tả nhân vật đó bằng lời
(không dùng `<Picture N>`), nên clip đó sẽ kém ổn định hơn về mặt nhân vật.
