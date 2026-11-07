# Danh sách cảnh tự viết — dùng khi bạn không muốn model tự chia.
#
# Chạy:
#   .\venv_autogen\Scripts\python.exe script_prompt_minimax.py --input vi_du_dau_vao.md --scenes vi_du_canh.md
#
# Mỗi dòng đúng 4 phần, ngăn bởi dấu |:
#   CLIP <số> | <hành động cụ thể, thấy được trên hình> | <nhân vật chính> | <không gian + ánh sáng>
#
# Lưu ý:
# - Hành động phải là việc làm, không phải trạng thái: "mở sổ, lật ba trang, khựng lại".
# - Nhân vật ghi đúng tên trong mục Characters của truyện.
# - Mỗi dòng ≈ 10 giây phim. Không gộp 2 sự việc rời vào 1 dòng.

CLIP 1 | Ông Tư đặt chiếc đèn lồng xuống bậc gỗ, tắt lửa bằng hai ngón tay rồi quay lưng đi | Ông Tư | bến tàu ban đêm, sương mù, đèn lồng vàng
CLIP 2 | Hải ngồi ở bàn sửa máy, lau tay vào giẻ, rồi cầm cuốn sổ lên lật trang cuối | Hải | xưởng sửa máy, đèn dầu, mưa ngoài mái tôn
CLIP 3 | Lam đứng ở cửa quán cà phê, nhìn theo Hải, tay siết quai túi | Lam | quán cà phê, ánh nến, mưa nhỏ
