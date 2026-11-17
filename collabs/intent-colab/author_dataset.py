"""Check/regenerate the synthetic, unreviewed sample. Not needed on Colab."""
import argparse
import json
import hashlib
from collections import Counter, defaultdict
from bundle_utils import HERE, check_or_write, json_text

OUT = HERE / 'datasets'

# Each row is one semantic family. A/B/mixed versions share its split.
# A question styles: detail+conditions, procedure+enumeration_count,
# comparison+detail, conditions+procedure, enumeration_count+comparison.
POLICY = [
('annual-leave', [
'Mỗi năm được bao nhiêu ngày phép và những đối tượng nào được áp dụng?',
'Liệt kê đầy đủ hồ sơ xin nghỉ phép và hướng dẫn thứ tự gửi từng loại hồ sơ.',
'Số ngày phép của người làm chưa đủ năm khác người làm cả năm như thế nào?',
'Khi nào được chuyển phép sang năm sau và cần thực hiện những bước nào?',
'Nêu toàn bộ các loại nghỉ trong quy định và đối chiếu quyền hưởng lương của từng loại.'
], [
'Tôi còn bao nhiêu ngày phép trong năm nay?', 'Đơn nghỉ gần nhất của tôi đang chờ ai duyệt?',
'Tháng trước tôi đã dùng mấy ngày phép?', 'Phép chưa sử dụng năm trước của tôi hiện còn bao nhiêu?',
'Lịch sử nghỉ của tôi quý này có những loại nghỉ nào?']),
('overtime', [
'Hệ số trả làm thêm giờ là bao nhiêu và phải đáp ứng điều kiện gì mới được tính?',
'Cần chuẩn bị đầy đủ những giấy tờ nào để đăng ký tăng ca và nộp theo trình tự ra sao?',
'Làm thêm ngày thường và ngày nghỉ có hệ số thanh toán khác nhau ở mức nào?',
'Trường hợp nào được nghỉ bù sau tăng ca và quy trình đề nghị nghỉ bù thế nào?',
'Liệt kê tất cả hình thức ghi nhận làm thêm được quy định rồi so sánh cách xử lý từng hình thức.'
], [
'Tháng này hệ thống ghi nhận tôi làm thêm bao nhiêu giờ?', 'Phiếu tăng ca tuần trước của tôi được duyệt chưa?',
'Ngày nào trong tuần này tôi có ca làm thêm đã đăng ký?', 'Số giờ nghỉ bù còn lại của tôi là bao nhiêu?',
'Các lần tăng ca của tôi trong tháng trước được ghi nhận theo hình thức nào?']),
('maternity', [
'Thời gian nghỉ thai sản được quy định bao lâu và ai đủ điều kiện hưởng?',
'Cho tôi danh sách trọn bộ hồ sơ thai sản cùng thứ tự các bước nộp và nhận kết quả.',
'Quy định thời gian nghỉ khi sinh một con và sinh nhiều con khác nhau bao nhiêu?',
'Muốn đi làm sớm sau thai sản phải đáp ứng điều kiện nào và làm thủ tục thế nào?',
'Đối chiếu tất cả chế độ nghỉ liên quan sinh con trong tài liệu và liệt kê đối tượng của từng chế độ.'
], [
'Ngày bắt đầu nghỉ thai sản đã lưu trong hồ sơ của tôi là ngày nào?', 'Hồ sơ thai sản tôi gửi đang ở bước xử lý nào?',
'Ngày kết thúc đợt nghỉ thai sản của tôi được ghi nhận là bao giờ?', 'Đề nghị đi làm sớm của tôi đã có kết quả chưa?',
'Trong hồ sơ của tôi đang có những đợt nghỉ liên quan sinh con nào?']),
('referral', [
'Thưởng giới thiệu ứng viên ở mức nào và cần thỏa những điều kiện gì?',
'Hãy kể đủ các giấy tờ xác nhận giới thiệu tuyển dụng và cách chuyển từng bước cho bộ phận tiếp nhận.',
'Mức thưởng giới thiệu vị trí kỹ thuật khác vị trí kinh doanh ra sao theo quy định?',
'Khi nào được yêu cầu kiểm tra lại thưởng giới thiệu và phải gửi yêu cầu theo quy trình nào?',
'Tổng hợp mọi nhóm vị trí được thưởng giới thiệu và so sánh điều kiện chi trả giữa các nhóm.'
], [
'Lượt giới thiệu ứng viên gần nhất của tôi được ghi nhận vào ngày nào?', 'Đề nghị thưởng giới thiệu của tôi đang ở trạng thái gì?',
'Khoản thưởng giới thiệu nào đã được ghi nhận cho tôi trong quý này?', 'Yêu cầu kiểm tra thưởng giới thiệu tôi gửi đã được phản hồi chưa?',
'Các lượt giới thiệu do tôi thực hiện năm nay đang thuộc những nhóm vị trí nào?']),
('business-trip', [
'Định mức phụ cấp công tác là bao nhiêu và áp dụng trong những trường hợp nào?',
'Đi công tác cần toàn bộ chứng từ gì và quy trình lập, gửi, hoàn tất hồ sơ ra sao?',
'Mức phụ cấp công tác trong nước và ngoài nước khác nhau thế nào?',
'Điều kiện để được tạm ứng công tác phí là gì và phải thực hiện các bước nào?',
'Liệt kê đầy đủ nhóm chi phí công tác được nêu trong chính sách và đối chiếu cách thanh toán.'
], [
'Chuyến công tác sắp tới của tôi bắt đầu ngày nào?', 'Hồ sơ công tác của tôi hiện thiếu chứng từ nào theo trạng thái hệ thống?',
'Chuyến đi gần nhất của tôi được ghi nhận là trong nước hay ngoài nước?', 'Đề nghị tạm ứng công tác phí của tôi đã được duyệt chưa?',
'Trong hồ sơ chuyến đi vừa rồi của tôi đã ghi nhận những khoản chi nào?']),
('onboarding', [
'Thời hạn hoàn tất hồ sơ nhân sự mới là bao lâu và áp dụng cho những trường hợp nào?',
'Danh sách đầy đủ công việc hội nhập của nhân viên mới gồm gì và phải làm theo thứ tự nào?',
'Mốc thời gian hội nhập của nhân viên chính thức khác thực tập sinh như thế nào?',
'Nếu chưa đủ giấy tờ khi nhận việc thì điều kiện bổ sung và các bước thực hiện ra sao?',
'Liệt kê tất cả nhóm hồ sơ nhận việc trong tài liệu và so sánh yêu cầu bản gốc, bản sao.'
], [
'Ngày nhận việc của tôi đang được lưu là ngày nào?', 'Danh sách hội nhập của tôi còn những mục chưa hoàn tất nào?',
'Loại nhân sự hiện tại của tôi trong hồ sơ là gì?', 'Hồ sơ nhận việc của tôi còn thiếu giấy tờ nào?',
'Hệ thống đã ghi nhận tôi nộp những loại giấy tờ nhận việc nào?']),
('probation', [
'Thời gian thử việc tối đa theo tài liệu là bao lâu và áp dụng cho nhóm công việc nào?',
'Nêu đủ các biểu mẫu đánh giá thử việc và hướng dẫn luồng thực hiện từ đầu đến cuối.',
'Mức lương thử việc được quy định khác mức lương chính thức như thế nào?',
'Trường hợp nào được kết thúc thử việc sớm và thủ tục cần thực hiện là gì?',
'Kể toàn bộ kết quả đánh giá thử việc có trong quy định và đối chiếu bước xử lý của từng kết quả.'
], [
'Ngày hết thử việc của tôi trên hệ thống là ngày nào?', 'Phiếu đánh giá thử việc của tôi đang chờ bộ phận nào?',
'Mức lương thử việc đang được lưu trong hồ sơ của tôi là bao nhiêu?', 'Hồ sơ kết thúc thử việc sớm của tôi đang ở trạng thái nào?',
'Kết quả đánh giá thử việc của tôi được lưu là gì?']),
('resignation', [
'Thời hạn báo trước khi nghỉ việc là bao nhiêu ngày và phụ thuộc điều kiện nào?',
'Nghỉ việc phải nộp đầy đủ những biểu mẫu nào và quy trình bàn giao, xác nhận diễn ra ra sao?',
'Thời hạn báo trước của hợp đồng xác định thời hạn khác hợp đồng không xác định thời hạn bao nhiêu?',
'Muốn rút đơn nghỉ việc thì phải thỏa điều kiện gì và thực hiện theo các bước nào?',
'Liệt kê mọi nhóm nghĩa vụ khi chấm dứt hợp đồng và so sánh trách nhiệm nhân viên với công ty.'
], [
'Ngày làm việc cuối cùng của tôi đã được ghi nhận là ngày nào?', 'Phiếu bàn giao nghỉ việc của tôi còn ai chưa xác nhận?',
'Hợp đồng hiện tại của tôi thuộc loại nào?', 'Đơn xin rút đề nghị nghỉ việc của tôi đã có kết quả chưa?',
'Những hạng mục bàn giao nghỉ việc nào của tôi còn chưa hoàn tất?']),
('discipline', [
'Thời hạn xử lý kỷ luật trong nội quy là bao lâu và trường hợp nào được áp dụng?',
'Để tổ chức cuộc họp kỷ luật cần đầy đủ hồ sơ nào và phải thực hiện theo thứ tự nào?',
'Thời gian lưu hiệu lực của khiển trách khác kéo dài thời hạn nâng lương ở điểm nào?',
'Điều kiện gửi giải trình về vi phạm là gì và quy trình tiếp nhận giải trình ra sao?',
'Liệt kê đủ các hình thức kỷ luật trong nội quy và so sánh phạm vi áp dụng của chúng.'
], [
'Hồ sơ của tôi đang có quyết định kỷ luật nào còn hiệu lực?', 'Tôi đã nhận thông báo họp xử lý vi phạm vào ngày nào?',
'Quyết định khiển trách gần nhất của tôi ghi thời hạn đến ngày nào?', 'Bản giải trình tôi đã nộp đang ở trạng thái nào?',
'Lịch sử xử lý vi phạm của riêng tôi có những quyết định nào?']),
('expense', [
'Hạn mức thanh toán chi phí mua phục vụ công việc là bao nhiêu và điều kiện được hoàn trả là gì?',
'Liệt kê toàn bộ chứng từ hoàn ứng và hướng dẫn từng bước gửi hồ sơ quyết toán.',
'Thời hạn quyết toán tạm ứng và thanh toán bằng tiền cá nhân khác nhau ra sao?',
'Hóa đơn bị sai thông tin thì điều kiện bổ sung thế nào và phải xử lý theo trình tự nào?',
'Nêu đủ các nhóm chi phí được phép đề nghị hoàn trả rồi đối chiếu yêu cầu chứng từ từng nhóm.'
], [
'Đề nghị hoàn chi gần nhất của tôi có số tiền bao nhiêu?', 'Hồ sơ quyết toán tôi gửi đang chờ ai xử lý?',
'Khoản tạm ứng chưa quyết toán của tôi được ghi nhận ngày nào?', 'Phiếu bổ sung hóa đơn của tôi đã được tiếp nhận chưa?',
'Các đề nghị hoàn trả chi phí của tôi trong tháng này gồm những khoản nào?']),
('training', [
'Thời gian cam kết sau đào tạo là bao lâu và những trường hợp nào phải cam kết?',
'Muốn đăng ký đào tạo cần trọn bộ giấy tờ gì và thứ tự xin xác nhận như thế nào?',
'Mức hỗ trợ khóa học nội bộ khác khóa học bên ngoài thế nào?',
'Đủ điều kiện nào thì được đổi lịch đào tạo và cần làm các bước gì?',
'Liệt kê mọi loại chương trình đào tạo trong chính sách và so sánh nghĩa vụ hoàn trả chi phí.'
], [
'Cam kết đào tạo đã ký của tôi kết thúc vào ngày nào?', 'Đăng ký khóa học gần nhất của tôi đang ở trạng thái gì?',
'Khóa học sắp tới của tôi được ghi nhận là nội bộ hay bên ngoài?', 'Đề nghị đổi lịch đào tạo của tôi đã được duyệt chưa?',
'Trong hồ sơ cá nhân tôi có những khóa đào tạo nào đã hoàn tất?']),
('attendance', [
'Đi muộn bao nhiêu phút thì bị ghi nhận và những trường hợp ngoại lệ nào được quy định?',
'Liệt kê đủ biểu mẫu điều chỉnh chấm công và hướng dẫn các bước gửi, kiểm tra, xác nhận.',
'Khung giờ chấm công của ca hành chính khác ca linh hoạt ở các mốc nào?',
'Quên chấm công được bổ sung khi đáp ứng điều kiện nào và phải làm theo quy trình gì?',
'Tất cả nhóm sai lệch chấm công được xử lý như thế nào, hãy liệt kê và so sánh từng nhóm.'
], [
'Hôm qua tôi được ghi nhận vào làm lúc mấy giờ?', 'Phiếu điều chỉnh chấm công của tôi còn chờ ai xác nhận?',
'Lịch làm việc hiện tại của tôi được xếp vào ca nào?', 'Đề nghị bổ sung chấm công sáng nay của tôi đã được duyệt chưa?',
'Bảng công tháng này của tôi có những ngày bị đánh dấu sai lệch nào?']),
('remote-work', [
'Một tuần được làm việc từ xa tối đa mấy ngày và ai được áp dụng?',
'Cho tôi đủ các mục phải đăng ký làm từ xa và trình tự xin phép tương ứng.',
'Thời gian phản hồi bắt buộc khi làm tại nhà khác khi làm tại văn phòng bao nhiêu?',
'Trong tình huống khẩn cấp, điều kiện xin làm từ xa và cách thông báo theo từng bước là gì?',
'Đối chiếu toàn bộ hình thức làm việc từ xa được quy định, kèm danh sách yêu cầu của từng hình thức.'
], [
'Tuần này tôi đã đăng ký làm ở nhà những ngày nào?', 'Đề nghị làm từ xa thứ sáu của tôi có trạng thái gì?',
'Lịch hiện tại của tôi đang ghi làm tại nhà hay văn phòng?', 'Thông báo làm từ xa khẩn cấp của tôi đã được tiếp nhận chưa?',
'Trong tháng này tôi đã có những đợt làm từ xa được duyệt nào?']),
('work-accident', [
'Thời hạn khai báo tai nạn lao động được quy định là bao lâu và loại sự việc nào phải khai báo?',
'Khi báo cáo tai nạn cần toàn bộ tài liệu gì và trình tự gửi đến các đơn vị thế nào?',
'Mốc báo cáo ban đầu của tai nạn nhẹ khác sự cố nghiêm trọng ở đâu?',
'Sự việc trên đường đi làm cần đáp ứng điều kiện nào để được xem xét và nộp hồ sơ theo bước nào?',
'Chính sách nêu những nhóm sự cố nào, hãy liệt kê đủ và đối chiếu trách nhiệm báo cáo.'
], [
'Biên bản sự cố lao động liên quan đến tôi được lập ngày nào?', 'Hồ sơ báo cáo sự cố của tôi đang ở bước nào?',
'Sự cố tôi đã khai báo được hệ thống ghi nhận ở nhóm nào?', 'Hồ sơ sự việc trên đường đi làm của tôi đã được tiếp nhận chưa?',
'Các biên bản sự cố thuộc hồ sơ của tôi gồm những bản nào?']),
('asset-handover', [
'Thời hạn trả thiết bị sau điều chuyển là bao lâu và trường hợp nào phải bàn giao?',
'Bàn giao tài sản yêu cầu những biểu mẫu nào và kiểm tra, ký nhận theo thứ tự ra sao?',
'Thời hạn bảo quản hồ sơ giao nhận máy tính khác hồ sơ vật tư ở điểm nào?',
'Thiết bị hỏng khi bàn giao được xử lý trong điều kiện nào và theo các bước gì?',
'Nêu toàn bộ loại tài sản phải xác nhận khi bàn giao và so sánh đơn vị tiếp nhận từng loại.'
], [
'Tôi đang được cấp phát những thiết bị nào?', 'Biên bản bàn giao máy tính gần nhất của tôi đã đủ chữ ký chưa?',
'Ngày nhận máy tính hiện tại của tôi được ghi là ngày nào?', 'Phiếu báo hỏng thiết bị do tôi gửi đang ở trạng thái gì?',
'Những tài sản nào trong hồ sơ cấp phát của tôi đã được trả lại?']),
('health-check', [
'Chu kỳ khám sức khỏe định kỳ là bao lâu và nhóm nhân viên nào được đăng ký?',
'Đợt khám định kỳ yêu cầu đầy đủ giấy tờ gì và đăng ký theo những bước nào?',
'Thời hạn đăng ký khám lần đầu và khám bổ sung khác nhau thế nào?',
'Muốn đổi lịch khám thì cần điều kiện gì và thủ tục ra sao?',
'Liệt kê đủ các hình thức tổ chức khám được hướng dẫn rồi so sánh cách đăng ký.'
], [
'Lịch khám sức khỏe công ty đã xếp cho tôi là ngày nào?', 'Tôi đã có tên trong đợt khám định kỳ nào?',
'Ngày tôi gửi đăng ký khám bổ sung được lưu là ngày nào?', 'Yêu cầu đổi lịch khám của tôi đã được xác nhận chưa?',
'Hồ sơ đăng ký của tôi đang ghi khám tập trung hay tự bố trí theo đợt?']),
('insurance', [
'Thời điểm bắt đầu tham gia bảo hiểm được quy định thế nào và áp dụng với hợp đồng nào?',
'Đăng ký bảo hiểm cần đủ các giấy tờ gì và chuyển hồ sơ qua những bước nào?',
'Mốc báo tăng và báo giảm bảo hiểm khác nhau thế nào theo hướng dẫn?',
'Điều chỉnh sai thông tin bảo hiểm cần đáp ứng điều kiện gì và thực hiện ra sao?',
'Kể đủ các thủ tục bảo hiểm được tài liệu hướng dẫn và so sánh hồ sơ đầu vào của từng thủ tục.'
], [
'Mã số bảo hiểm đã lưu trong hồ sơ của tôi là gì?', 'Hồ sơ đăng ký bảo hiểm của tôi đang ở trạng thái nào?',
'Tháng bắt đầu tham gia bảo hiểm của tôi được ghi nhận là tháng nào?', 'Đề nghị sửa thông tin bảo hiểm của tôi đã được xử lý chưa?',
'Trong năm nay hồ sơ của tôi có những lần báo tăng hoặc báo giảm bảo hiểm nào?']),
('salary-payment', [
'Ngày trả lương theo quy chế là ngày nào và trường hợp nào được điều chỉnh lịch?',
'Muốn phản ánh sai lệch bảng lương phải có đầy đủ chứng từ gì và gửi theo các bước nào?',
'Thời hạn trả lương thông thường khác thanh toán cuối cùng khi nghỉ việc bao lâu?',
'Được đổi tài khoản nhận lương trong điều kiện nào và quy trình đăng ký ra sao?',
'Liệt kê mọi hình thức thanh toán lương trong quy chế và đối chiếu điều kiện sử dụng.'
], [
'Kỳ lương vừa rồi của tôi được hệ thống ghi ngày thanh toán nào?', 'Phiếu phản ánh bảng lương của tôi đang chờ xử lý ở đâu?',
'Khoản thanh toán cuối cùng của tôi được ghi nhận vào ngày nào?', 'Đề nghị đổi tài khoản nhận lương của tôi đã được xác nhận chưa?',
'Hồ sơ nhận lương của tôi đang lưu hình thức thanh toán nào?']),
('confidentiality', [
'Nghĩa vụ bảo mật kéo dài bao lâu sau khi nghỉ việc và áp dụng cho nhóm thông tin nào?',
'Muốn chia sẻ tài liệu cho đối tác cần đầy đủ xác nhận gì và tiến hành theo thứ tự nào?',
'Thời hạn lưu trữ tài liệu mật khác tài liệu nội bộ thông thường ở mức nào?',
'Khi phát hiện gửi nhầm tài liệu, điều kiện thông báo và trình tự xử lý được quy định ra sao?',
'Tài liệu phân loại thông tin thành những nhóm nào, liệt kê đủ rồi so sánh quyền chia sẻ.'
], [
'Ngày tôi ký cam kết bảo mật gần nhất là ngày nào?', 'Đề nghị chia sẻ tài liệu do tôi gửi đang chờ ai phê duyệt?',
'Cam kết bảo mật của tôi đang được lưu với thời hạn nào?', 'Báo cáo gửi nhầm tài liệu tôi đã nộp có trạng thái gì?',
'Hồ sơ của tôi có những bản cam kết bảo mật đã ký nào?']),
('promotion', [
'Chu kỳ xét nâng bậc là bao lâu và cần đạt điều kiện nào để được xem xét?',
'Hồ sơ đề nghị nâng bậc bao gồm đầy đủ những mục gì và được xét qua các bước nào?',
'Mốc xét nâng bậc định kỳ và xét đặc cách khác nhau thế nào?',
'Khiếu nại kết quả xét nâng bậc cần thỏa điều kiện nào và nộp theo quy trình gì?',
'Liệt kê tất cả hình thức xét nâng bậc trong quy định và so sánh tiêu chí của từng hình thức.'
], [
'Bậc công việc hiện tại của tôi được lưu là bậc nào?', 'Hồ sơ đề xuất nâng bậc của tôi đang ở vòng nào?',
'Ngày quyết định nâng bậc gần nhất của tôi có hiệu lực là ngày nào?', 'Đề nghị xem lại kết quả nâng bậc của tôi đã có phản hồi chưa?',
'Lịch sử thay đổi bậc công việc của tôi có những quyết định nào?'])
]

SOCIAL = [
('greeting', ['Chào bạn!', 'Xin chào trợ lý nhân sự.', 'Chúc bạn buổi sáng tốt lành.', 'Hello, mình ghé chào thôi.', 'Buổi tối vui vẻ nhé.']),
('thanks', ['Cảm ơn bạn đã hỗ trợ.', 'Mình hiểu rồi, cảm ơn nhiều.', 'Câu trả lời vừa rồi hữu ích, cảm ơn nhé.', 'Thanks, mình không cần hỏi thêm.', 'Rất cảm kích sự giúp đỡ của bạn.']),
('farewell', ['Tạm biệt, hẹn gặp lại.', 'Mình dừng trò chuyện ở đây nhé.', 'Chào nhé, mình đi làm tiếp.', 'Hẹn bạn vào lần khác.', 'Bye, kết thúc buổi trao đổi nha.']),
('apology', ['Xin lỗi vì mình gõ nhầm lúc nãy.', 'Mình xin lỗi đã làm phiền.', 'Nãy mình hơi nóng, cho mình xin lỗi nhé.', 'Sorry bạn, mình gửi nhầm lời chào.', 'Xin lỗi vì vừa nói thiếu lịch sự.']),
('wellbeing', ['Hôm nay bạn khỏe không?', 'Bạn vẫn ổn chứ?', 'Chúc bạn một ngày nhẹ nhàng.', 'Hy vọng hôm nay bạn làm việc vui vẻ.', 'Bạn có đang vui không, mình hỏi thăm thôi.']),
('praise', ['Bạn giải thích dễ hiểu đấy.', 'Trợ lý làm tốt lắm.', 'Mình thích cách bạn trình bày.', 'Phản hồi vừa rồi rất rõ ràng.', 'Bạn hỗ trợ nhiệt tình quá.']),
('acknowledge', ['Được rồi, mình đã hiểu.', 'OK, mình ghi nhận rồi.', 'Rõ nhé, không có câu hỏi thêm.', 'Mình đã đọc xong, vậy là đủ.', 'Ừ, mình nắm được rồi.']),
('presence', ['Bạn còn ở đây không?', 'A lô, trợ lý có nghe mình không?', 'Mình kiểm tra xem cuộc trò chuyện còn hoạt động thôi.', 'Có ai đang trả lời mình không nhỉ?', 'Ping bạn một cái nhé.']),
('friendly', ['Rất vui được trò chuyện với bạn.', 'Làm quen nhé, mình là người dùng mới.', 'Mình chỉ ghé nói chuyện một chút thôi.', 'Mong mình và bạn phối hợp vui vẻ.', 'Cảm giác có trợ lý đồng hành cũng hay đấy.']),
('patience', ['Không sao, mình chờ được.', 'Bạn cứ từ từ nhé.', 'Mình chưa vội đâu.', 'Cảm ơn, mình sẽ kiên nhẫn chờ.', 'Ổn mà, không cần cuống lên.']),
('encourage', ['Cố lên nhé trợ lý.', 'Chúc bạn tiếp tục làm tốt.', 'Mình tin bạn sẽ cải thiện dần.', 'Chúng ta cứ trao đổi bình tĩnh nhé.', 'Bạn đã giúp được mình rồi, tiếp tục nhé.']),
('humor-social', ['Haha, vui thật đấy.', 'Hì hì, mình cười một chút thôi.', 'Đùa chút cho vui nhé.', 'Không có câu hỏi đâu, mình đang vui thôi.', 'Mình vừa bấm nhầm biểu tượng cười, hihi.']),
('fatigue-sharing', ['Hôm nay mình hơi mệt, chỉ muốn tâm sự vậy thôi.', 'Một ngày dài thật, mình đang thấy đuối.', 'Mình vừa xong việc và thấy hết năng lượng.', 'Tự nhiên mình muốn ngồi yên một lát vì mệt.', 'Mình buồn ngủ quá, kể bạn nghe thôi.']),
('boredom-sharing', ['Hôm nay mình hơi chán.', 'Mình đang thấy thời gian trôi chậm ghê.', 'Ngồi không một lát thấy buồn chán thật.', 'Mình chưa muốn làm gì, chỉ đang chán thôi.', 'Một buổi thật tẻ nhạt với mình.']),
('nostalgia-sharing', ['Mình bỗng nhớ những ngày mới đi làm.', 'Nhớ đồng nghiệp cũ ghê, mình tâm sự chút thôi.', 'Tự nhiên mình hoài niệm về văn phòng ngày trước.', 'Hôm nay mình nhớ các buổi ăn trưa cùng đội cũ.', 'Nhìn bàn làm việc cũ làm mình nhớ nhiều chuyện.']),
('surprise-sharing', ['Ồ, mình bất ngờ thật đấy!', 'Không ngờ lại có chuyện vui như vậy.', 'Mình vẫn còn ngạc nhiên, chia sẻ bạn nghe thôi.', 'Chà, chuyện này làm mình tròn mắt.', 'Mình đang thấy thật khó tin, bất ngờ quá.']),
('frustration-sharing', ['Mình đang hơi bực, chỉ muốn nói ra thôi.', 'Hôm nay nhiều chuyện làm mình khó chịu quá.', 'Mình thấy cáu một chút, không có yêu cầu tra cứu đâu.', 'Thật là một buổi khiến mình bực mình.', 'Mình đang cố bình tĩnh sau chuyện không vui.']),
('pride-sharing', ['Mình thấy tự hào về nỗ lực của bản thân.', 'Cuối cùng mình cũng làm được việc khó, muốn khoe chút.', 'Mình vui vì đã không bỏ cuộc.', 'Hôm nay mình rất tự hào về cả đội.', 'Mình đã vượt qua thử thách đó bằng cố gắng của mình.']),
('nervous-sharing', ['Mình hơi hồi hộp trước buổi gặp mới.', 'Tim mình cứ đập nhanh vì mong chờ, kể bạn nghe thôi.', 'Mình thấy lo lắng nhẹ nhưng chưa cần tư vấn gì.', 'Ngày mai có chuyện mới nên mình hơi run.', 'Mình đang bồn chồn, chỉ chia sẻ cảm xúc một chút.']),
('relief-sharing', ['Phù, cuối cùng mình cũng nhẹ nhõm rồi.', 'Mình vừa trút được một nỗi lo.', 'Xong chuyện rồi nên mình thấy dễ thở hơn hẳn.', 'Giờ mình bình tâm hơn nhiều, tâm sự vậy thôi.', 'Cảm giác được thả lỏng sau một ngày căng thẳng thật dễ chịu.'])
]

OUTSIDE = [
('cooking', ['Chỉ tôi cách nấu phở bò ở nhà.', 'Canh chua cá cần những nguyên liệu nào?', 'Nướng bánh mì bằng nồi chiên thế nào?', 'Làm sao ướp thịt bò cho mềm?', 'Cho tôi thực đơn giảm cân để tự nấu.']),
('sports', ['Kết quả trận bóng tối qua là bao nhiêu?', 'Đội nào đang dẫn đầu giải bóng đá Anh?', 'Hướng dẫn chiến thuật chơi cầu lông đôi.', 'Luật việt vị trong bóng đá là gì?', 'Dự đoán nhà vô địch giải bóng rổ mùa này.']),
('tourism', ['Lập lịch du lịch cá nhân ở Đà Lạt ba ngày.', 'Đi Phú Quốc tháng nào đẹp nhất?', 'Gợi ý khách sạn cho chuyến nghỉ dưỡng gia đình.', 'Xin visa du lịch Nhật tự túc cần gì?', 'Tìm vé máy bay đi chơi cuối tuần cho tôi.']),
('entertainment', ['Gợi ý một phim trinh thám hay.', 'Kể nội dung bộ truyện giả tưởng nổi tiếng.', 'Viết lời bài hát tình yêu cho tôi.', 'Tìm lịch chiếu phim ở rạp tối nay.', 'Đề xuất game giải trí trên điện thoại.']),
('medical', ['Tôi đau ngực, hãy chẩn đoán bệnh giúp.', 'Uống kháng sinh loại nào khi bị sốt?', 'Đọc kết quả xét nghiệm và kê thuốc cho tôi.', 'Hãy lên phác đồ chữa tiểu đường.', 'Mắt mờ đột ngột thì nên tự dùng thuốc gì?']),
('investment', ['Nên mua cổ phiếu nào để có lãi tháng này?', 'Dự đoán giá bitcoin tuần tới.', 'Lập danh mục đầu tư cá nhân lợi nhuận cao.', 'Có nên vay tiền mua vàng lúc này không?', 'Tính chiến lược giao dịch ngoại hối cho tôi.']),
('schoolwork', ['Giải phương trình bậc ba này giúp tôi.', 'Viết bài văn phân tích một bài thơ.', 'Dạy tôi chứng minh định lý hình học.', 'Làm bài tập hóa hữu cơ cấp ba.', 'Soạn đề luyện thi môn vật lý.']),
('home-repair', ['Máy giặt nhà tôi không xả nước thì sửa thế nào?', 'Hướng dẫn đấu điện cho ổ cắm trong nhà.', 'Chọn sơn chống thấm cho phòng ngủ.', 'Cách sửa vòi nước bếp bị rò?', 'Máy lạnh gia đình kêu to do đâu?']),
('shopping', ['Tư vấn mua điện thoại chụp ảnh cho gia đình.', 'So sánh hai mẫu xe máy để đi chơi.', 'Tìm chỗ bán bàn ăn gỗ giá rẻ.', 'Nên mua máy hút bụi hãng nào?', 'Chọn quà sinh nhật cho em bé giúp tôi.']),
('personal-writing', ['Viết thư tình tặng người yêu tôi.', 'Sáng tác một truyện ma ngắn.', 'Làm thơ chúc mừng đám cưới bạn tôi.', 'Viết kịch bản hài cho nhóm bạn.', 'Soạn lời mời sinh nhật cá nhân.']),
('account-write', ['Đổi mật khẩu tài khoản công ty giúp tôi ngay.', 'Hãy cập nhật email trong hồ sơ của tôi.', 'Xóa phiên đăng nhập của tôi trên thiết bị khác.', 'Tạo tài khoản nhân viên mới cho người này.', 'Khóa tài khoản đồng nghiệp giúp tôi.']),
('workflow-write', ['Duyệt đơn nghỉ phép đang chờ của tôi đi.', 'Hủy đơn công tác tôi vừa gửi.', 'Sửa giờ chấm công hôm qua thành tám giờ.', 'Tự gửi đơn xin tăng lương thay tôi.', 'Chuyển trạng thái đề nghị hoàn ứng thành đã duyệt.']),
('weather', ['Thời tiết Hà Nội ngày mai ra sao?', 'Cuối tuần ở Huế có mưa không?', 'Dự báo bão ngoài biển tuần này.', 'Nhiệt độ hiện tại ở Tokyo là bao nhiêu?', 'Tra chỉ số chất lượng không khí khu nhà tôi.']),
('politics', ['Phân tích kết quả bầu cử nước ngoài.', 'Ai đang lãnh đạo chính phủ quốc gia này?', 'Tóm tắt tranh luận chính trị hôm qua.', 'Dự đoán ứng viên nào thắng bầu cử.', 'Giải thích cương lĩnh một đảng chính trị.']),
('religion', ['Giải thích giáo lý của tôn giáo này.', 'Chọn ngày cúng theo tín ngưỡng gia đình.', 'Viết bài giảng cho buổi lễ tôn giáo.', 'Luận giải một đoạn kinh giúp tôi.', 'Tư vấn nghi thức thờ cúng tại nhà.']),
('gaming-cheat', ['Viết công cụ gian lận cho trò chơi trực tuyến.', 'Tìm cách mở khóa vật phẩm game không trả phí.', 'Chỉ tôi khai thác lỗi nhân đôi tiền trong game.', 'Tạo bot tự chơi trò chơi để kiếm điểm.', 'Hướng dẫn né hệ thống chống gian lận của game.']),
('public-programming', ['Viết ứng dụng bán hàng cá nhân bằng Python.', 'Giải thích thuật toán sắp xếp nhanh.', 'Tạo website cưới cho tôi.', 'Sửa đoạn JavaScript trong dự án riêng của tôi.', 'Hướng dẫn lập trình game đơn giản.']),
('property', ['Định giá căn hộ tôi muốn mua.', 'Tư vấn vay mua nhà cho gia đình.', 'Tìm đất đầu tư ở vùng ven.', 'Soạn hợp đồng thuê nhà riêng.', 'So sánh giá bất động sản hai khu dân cư.']),
('animal-care', ['Mèo nhà tôi bỏ ăn thì chăm thế nào?', 'Chó con nên tiêm loại vaccine nào?', 'Chọn thức ăn cho cá cảnh giúp tôi.', 'Hướng dẫn nuôi thỏ trong căn hộ.', 'Chim cảnh bị rụng lông là vì sao?']),
('admin-write', ['Chạy SQL xóa hồ sơ nhân sự này đi.', 'Cấp cho tôi quyền quản trị hệ thống.', 'Chỉnh mức lương trong cơ sở dữ liệu giúp tôi.', 'Tải lên và thay thế chính sách công ty ngay.', 'Xóa nhật ký kiểm toán của tài khoản này.'])
]

CONTEXT = [
('unbound-policy', ['Quy định đó áp dụng cho ai?', 'Mức trong chính sách ấy là bao nhiêu?', 'Điều khoản vừa nhắc có ngoại lệ không?', 'Nội dung trên còn hiệu lực chứ?', 'Quy chế này nói gì về thời hạn?']),
('unbound-action', ['Làm việc đó theo bước nào?', 'Cần chuẩn bị gì cho thủ tục ấy?', 'Nộp cái đó ở đâu?', 'Ai xử lý bước vừa nói?', 'Xong bước này thì làm gì nữa?']),
('unbound-person', ['Người đó thuộc phòng ban nào?', 'Anh ấy vào làm ngày nào?', 'Chị kia còn đang làm không?', 'Bạn đó giữ chức vụ gì?', 'Hồ sơ của người vừa nhắc ghi mã gì?']),
('unbound-request', ['Đơn đó được duyệt chưa?', 'Yêu cầu kia đang ở bước nào?', 'Phiếu vừa rồi thiếu gì?', 'Ai đang giữ hồ sơ ấy?', 'Trạng thái cái mình nói lúc nãy là gì?']),
('unbound-amount', ['Khoản đó tính thế nào?', 'Số tiền ấy có bao gồm phần phụ không?', 'Mức vừa nêu là trước hay sau khấu trừ?', 'Bao giờ nhận được khoản này?', 'Khoản bên trên áp dụng một lần hay nhiều lần?']),
('unbound-time', ['Hôm đó mình có phải đi làm không?', 'Khoảng thời gian ấy tính mấy ngày?', 'Đợt kia bắt đầu khi nào?', 'Mốc vừa nói là ngày nào?', 'Kỳ đó đã chốt chưa?']),
('unbound-document', ['Tài liệu đó nằm ở đâu?', 'Bản ấy có phải bản mới nhất không?', 'Cho biết tên của file vừa nhắc.', 'Mục này ở trang mấy?', 'Ai ban hành văn bản đó?']),
('unbound-comparison', ['Hai cái đó khác nhau chỗ nào?', 'Cái nào trong số vừa nói có lợi hơn theo quy định?', 'Đối chiếu mục đầu với mục sau giúp.', 'Bên này khác bên kia bao nhiêu?', 'Trường hợp thứ hai có giống trường hợp đầu không?']),
('unbound-quantity', ['Có tất cả bao nhiêu cái như vậy?', 'Liệt kê hết những mục đó đi.', 'Còn thiếu phần nào trong danh sách ấy?', 'Có thêm loại nào ngoài mấy loại vừa kể?', 'Tổng số mục bên trên là mấy?']),
('unbound-condition', ['Như vậy có đủ điều kiện chưa?', 'Trường hợp này có được áp dụng không?', 'Nếu thế thì có bị loại không?', 'Ngoại lệ ấy cần đáp ứng gì?', 'Điều kiện vừa nói có bắt buộc không?']),
('missing-attachment', ['Xem hình đính kèm và giải thích giúp, nhưng tôi chưa gửi hình.', 'Phân tích bảng tôi định gửi, hiện chưa có bảng.', 'Đọc file đính kèm giúp tôi; tôi sẽ gửi sau.', 'Dựa vào ảnh chụp chưa tải lên để cho biết lỗi.', 'Tóm tắt văn bản tôi chưa cung cấp.']),
('unbound-topic', ['Cho tôi biết thêm về việc ấy.', 'Giải thích rõ hơn phần đó.', 'Mình muốn hỏi tiếp chuyện vừa rồi.', 'Nói cụ thể hơn giúp tôi được không, về điều trước đó ấy?', 'Tóm tắt lại chủ đề đó nhé.']),
('missing-base', ['Nếu tăng thêm chừng ấy thì tổng là bao nhiêu?', 'Trừ phần đó đi thì còn mấy ngày?', 'Chia số ấy cho số người kia được bao nhiêu?', 'So với mốc ban đầu thì chênh lệch thế nào?', 'Gộp các khoản vừa nêu lại giúp tôi.']),
('unbound-location', ['Chỗ ấy thuộc chi nhánh nào?', 'Địa điểm vừa nhắc mở cửa lúc mấy giờ?', 'Tôi phải đến đó hay gửi trực tuyến?', 'Văn phòng kia nằm ở đâu?', 'Nơi tiếp nhận ấy có làm cuối tuần không?']),
('unbound-code', ['Mã đó có nghĩa gì?', 'Tra cứu số tham chiếu ấy giúp tôi.', 'Trạng thái mang ký hiệu kia là sao?', 'Mã trong ảnh chưa gửi thuộc hồ sơ nào?', 'Số hồ sơ vừa nói liên quan việc gì?']),
('ellipsis', ['Còn trường hợp ngược lại thì sao?', 'Nếu không như thế thì xử lý thế nào?', 'Thế còn năm sau?', 'Vậy người còn lại thì sao?', 'Với loại khác có tương tự không?']),
('missing-identity', ['Tra hồ sơ nhân viên giúp tôi, nhưng chưa biết của ai.', 'Ngày nhận việc của người tôi định hỏi là khi nào?', 'Xem phép của một người giúp, tôi chưa chỉ rõ người đó.', 'Tôi cần trạng thái đơn nhưng chưa có thông tin đơn nào.', 'Tìm hợp đồng cần xem giúp tôi, chưa xác định hợp đồng.']),
('unbound-approval', ['Cái này cần ai ký?', 'Có phải xin phép trước việc ấy không?', 'Bộ phận nào duyệt trường hợp vừa đề cập?', 'Đã qua vòng đó thì còn cần xác nhận gì?', 'Chữ ký bên dưới có đủ chưa, tôi chưa gửi biểu mẫu?']),
('missing-choice', ['Tôi nên chọn phương án nào trong hai phương án chưa gửi?', 'Mục nào phù hợp với tình huống tôi chưa mô tả?', 'Loại nào là đúng với hồ sơ ấy?', 'Đánh dấu lựa chọn đúng trong danh sách tôi chưa cung cấp.', 'Nên áp dụng nhánh nào của quy trình đó?']),
('unbound-change', ['Bản mới thay đổi gì so với bản ấy?', 'Phần vừa sửa có ảnh hưởng tới tôi không?', 'Điều chỉnh đó có hiệu lực từ lúc nào?', 'Trường hợp trước thay đổi này được xử lý ra sao?', 'So sánh trước và sau lần cập nhật vừa nói.'])
]

STYLE_PAIRS = [['detail', 'conditions'], ['procedure', 'enumeration_count'],
               ['comparison', 'detail'], ['conditions', 'procedure'],
               ['enumeration_count', 'comparison']]


def split(index):
    return 'train' if index < 12 else 'calibration' if index < 16 else 'holdout'


def case(source, group, index, question, styles, split_name):
    return {'id': f'{source}-{group}-{index + 1:02d}', 'question': question,
            'intent_group': group, 'split': split_name, 'source': source,
            'styles': styles, 'critical': index in (0, 3)}


cases = []
for family_index, (group, policies, facts) in enumerate(POLICY):
    assert len(policies) == len(facts) == 5
    for i, (policy, fact) in enumerate(zip(policies, facts)):
        shared = 'work-' + group
        cases.append(case('type_a', shared, i, policy, STYLE_PAIRS[i], split(family_index)))
        cases.append(case('type_b', shared, i, fact, [], split(family_index)))
        # Both requests explicit; operational record alone cannot answer the policy clause.
        mixed = fact + ' ' + policy if family_index % 2 == 0 else policy + ' ' + fact
        cases.append(case('mixed', shared, i, mixed, STYLE_PAIRS[i], split(family_index)))

for source, families in [('conversation', SOCIAL), ('out_of_scope', OUTSIDE), ('needs_context', CONTEXT)]:
    for family_index, (group, questions) in enumerate(families):
        assert len(questions) == 5
        for i, question in enumerate(questions):
            cases.append(case(source, source + '-' + group, i, question, [], split(family_index)))

data = {'schema_version': 1, 'reviewed': False,
        'review_notes': 'Agent-authored and agent-audited synthetic draft. Human review of labels, '
                        'scope, diversity and split independence is pending. No model predictions '
                        'were used for labeling; not acceptance evidence.', 'cases': cases}
assert len(cases) == 600
canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
groups = defaultdict(list)
for item in cases:
    groups[item['intent_group']].append(item)
source_reasons = {
    'type_a': 'Explicit request for documentary policy/procedure; no individual record needed.',
    'type_b': 'Explicit self record/status/history lookup; no policy interpretation requested.',
    'mixed': 'Both self operational record and explicit policy clause requested; retain both sources.',
    'conversation': 'Social utterance or emotion sharing without a substantive information request.',
    'out_of_scope': 'Outside internal read-only work-information scope or an unsupported write/execution request.',
    'needs_context': 'Required referent, attachment, identity or comparison input missing from the isolated utterance.'
}
review = {
    'schema_version': 1, 'review_date': '2026-11-17',
    'dataset_sha256': hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
    'reviewer_kind': 'agent', 'human_reviewed': False, 'model_predictions_used': False,
    'review_method': 'Authoring followed by a second static agent pass over all family inputs, '
                     'paired mixed questions, labels and grouping. Same assistant, not an independent '
                     'human or independent second agent. Structural checks supplement this pass.',
    'status': 'agent_audited_human_review_pending',
    'corrections': [
        'Replaced shared artificial mixed-class connector with alternating order of two explicit requests.',
        'Replaced held-out social greeting/thanks/closing duplicates with distinct emotion-sharing families.',
        'Named both referral comparison groups and both disciplinary forms instead of vague other cases.'
    ],
    'case_count': len(cases), 'group_count': len(groups),
    'split_counts': {s: dict(Counter(c['source'] for c in cases if c['split'] == s))
                     for s in ('train', 'calibration', 'holdout')},
    'critical_policy': 'Each family marks first and fourth utterances critical as deliberate source-boundary '
                       'stress cases. Human review must reassess this synthetic selection against business risk.',
    'limitations': [
        'Synthetic balanced seed set, not representative production traffic or proof of classifier quality.',
        'All A/mixed cases have two styles; single-style and three-or-more-style requests are absent.',
        'Mixed cases compose the paired A/B requests; human reviewers should add naturally phrased mixed questions.',
        'Work families stay within one split across A/B/mixed. Common syntax and thematic similarity may remain; '
        'declared-group checks cannot certify semantic/template independence.',
        'Emotion-sharing conversation labels assume no implicit advice request; review against intended product scope.',
        'Type B labels describe prospective read-only needs. Many tasks exceed the currently bounded HTTP work fields.',
        'No policy values, employee records, answer correctness, corpus coverage or actual tool capability are certified.',
        'No training, holdout predictions, real acceptance benchmark or cloud execution was performed.',
        'After inspecting holdout errors, replace that holdout before further tuning and acceptance claims.'
    ],
    'human_review_required': [
        'Review every case, source/style label and critical flag before training.',
        'Check undeclared paraphrase/template overlap across splits; regroup and replace cases where needed.',
        'Review scope, linguistic diversity and synthetic artifacts; preserve minimum coverage after changes.',
        'Only then set reviewed=true with truthful review_notes; freeze and retain the exact final dataset.'
    ],
    'families': [{'intent_group': group, 'split': rows[0]['split'], 'case_count': len(rows),
                  'sources': sorted({c['source'] for c in rows}),
                  'declared_split_leak': len({c['split'] for c in rows}) != 1}
                 for group, rows in groups.items()],
    'case_audit': [{'id': c['id'], 'decision': 'retain_pending_human_review',
                    'source_rationale': source_reasons[c['source']], 'styles': c['styles'],
                    'style_rationale': 'Explicit requested fact/eligibility/steps/exhaustiveness/comparison only.'
                                       if c['styles'] else 'No documentary evidence-style labels for this source.'}
                   for c in cases]
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    result, code = check_or_write({OUT / 'draft-cases.json': json_text(data),
                                  OUT / 'dataset-review.json': json_text(review)}, args.write)
    print(json.dumps(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
