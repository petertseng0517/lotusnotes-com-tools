"""
一次性範本準備（sdd5.md §4.5.1）：把 store/template-store.doc 裡的空白欄位換成
{{...}} 佔位符，另存成 store/templates/contract_template.doc，供 generate_contracts.py
套版使用。

用 Word COM 對已經確認過位置的段落做**限定範圍**的 Find/Replace，不是對整份文件
做普通的 Find/Replace——範本裡到處都是空白/重複字（例如「聯絡人：」「年」「月」
「日」都出現兩次以上），對整份文件做 Find/Replace 很容易誤換到不該換的地方；
這裡改成把每次搜尋範圍鎖定在單一段落內（`doc.Range(段落起點, 段落終點)`），只
在那個範圍裡找，找不到就直接中止並印出目前內容，不會默默改錯地方。

段落位置（1-based，對照 store/template-store.doc 目前的內容，用 win32com 直接
讀出來核對過）：
    4  「（以下簡稱乙方）」上方 → 店名
    6  「乙方提供以下優惠：」  → 優惠內容
    15 「六、本合約有效期間…」 → 合約起訖日（年/月/日 各出現兩次，起始日在前）
    22 甲方「聯絡人：」        → 固定值（職工福利小組聯絡窗口，非表單欄位）
    23 甲方「聯絡電話：」      → 固定值
    24 乙方「乙方：」          → 店名
    25 乙方「地址：」          → 地址
    26 乙方「負責人：」        → 負責人姓名
    27 乙方「統一編號：」      → 統一編號
    28 乙方「聯絡人：」        → 聯絡人
    29 乙方「聯絡電話：」      → 電話
    32 「中華民國 年 月 日」   → 簽約日期
    7~10（處理完以上所有段落後，最後一步刪除）→ 原本紙本表單留給人工手寫優惠內容
      的空白行，優惠內容已自動套版後變成多餘空白，會把版面往下擠、簽約日期可能被
      擠到自己獨占一頁，見 sdd5.md §4.5.1（2026-09-11 依實際套版結果調整）

**只需要執行一次**（`python prepare_contract_template.py`）；`store/template-store.doc`
之後如果改版，重跑前建議先用 Word 打開比對段落內容有沒有變，變了的話上面這份對照
表跟本檔案的段落編號都要跟著更新，否則會在找不到預期文字時直接中止並印出實際內容，
不會誤植到錯的地方。
"""
import os

import win32com.client as win32

BASE_DIR = os.path.dirname(__file__)
SOURCE_PATH = os.path.join(BASE_DIR, "template-store.doc")
OUTPUT_DIR = os.path.join(BASE_DIR, "templates")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "contract_template.doc")

# 職工福利小組聯絡窗口，固定值，不是表單欄位（sdd5.md §4.5.1、§6 已確認）。
HOSPITAL_CONTACT_PERSON = "曾建瑋"
HOSPITAL_CONTACT_PHONE = "03-8561825轉15295"

WD_FORMAT_DOCUMENT = 0  # wdFormatDocument（.doc）


def _replace_in_paragraph(doc, para_index_1based: int, target: str, replacement: str):
    """在指定段落的範圍內找 target 並替換成 replacement，範圍外的文字完全不會被搜尋到。"""
    para = doc.Paragraphs(para_index_1based)
    search_rng = doc.Range(para.Range.Start, para.Range.End)
    f = search_rng.Find
    f.ClearFormatting()
    f.Text = target
    f.Replacement.ClearFormatting()
    f.Replacement.Text = replacement
    f.Forward = True
    f.Wrap = 0  # wdFindStop：只在這個範圍內找，不要繞去文件其他地方
    if not f.Execute(Replace=1):  # wdReplaceOne
        raise SystemExit(
            f"段落 {para_index_1based} 找不到 {target!r}，範本可能已改版，需要重新核對欄位位置。\n"
            f"該段落目前內容：{doc.Paragraphs(para_index_1based).Range.Text!r}"
        )


def _replace_sequence_in_paragraph(doc, para_index_1based: int, pairs: list[tuple[str, str]]):
    """依序在同一段落內從左到右各自替換一次，同一個 target 出現多次時（例如合約
    起訖日「年/月/日」各出現兩次）用這支，不能用 _replace_in_paragraph 各自獨立呼叫
    ——那樣兩次呼叫都只會找到「第一個」，第二個永遠對不到（實測驗證過這個差異）。"""
    pos = doc.Paragraphs(para_index_1based).Range.Start
    for i, (target, replacement) in enumerate(pairs):
        para_end_now = doc.Paragraphs(para_index_1based).Range.End
        search_rng = doc.Range(pos, para_end_now)
        f = search_rng.Find
        f.ClearFormatting()
        f.Text = target
        f.Replacement.ClearFormatting()
        f.Replacement.Text = replacement
        f.Forward = True
        f.Wrap = 0
        if not f.Execute(Replace=1):
            raise SystemExit(
                f"段落 {para_index_1based} 依序替換第 {i + 1} 個樣式（{target!r} -> {replacement!r}）"
                f"時找不到，範本可能已改版。\n該段落目前內容：{doc.Paragraphs(para_index_1based).Range.Text!r}"
            )
        pos = search_rng.End


def main():
    if not os.path.exists(SOURCE_PATH):
        raise SystemExit(f"找不到 {SOURCE_PATH}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    word = win32.gencache.EnsureDispatch("Word.Application")
    word.Visible = False
    doc = word.Documents.Open(SOURCE_PATH)
    try:
        _replace_in_paragraph(doc, 4, "（以下簡稱乙方）", "{{store_name}}（以下簡稱乙方）")
        # 優惠內容前面加一個段落內換行（\x0b，不是段落結尾的 \r，不會影響後面段落編號），
        # 讓條列式優惠內容從下一行開始，不會跟「乙方提供以下優惠：」擠在同一行
        # （2026-09-11 依實際套版結果調整，見 sdd5.md §4.5.1）。
        _replace_in_paragraph(doc, 6, "乙方提供以下優惠：", "乙方提供以下優惠：\x0b{{discount_content}}")
        _replace_sequence_in_paragraph(doc, 15, [
            ("年", "{{start_y}}年"), ("月", "{{start_m}}月"), ("日", "{{start_d}}日"),
            ("年", "{{end_y}}年"), ("月", "{{end_m}}月"), ("日", "{{end_d}}日"),
        ])
        _replace_in_paragraph(doc, 22, "聯絡人：", f"聯絡人：{HOSPITAL_CONTACT_PERSON}")
        _replace_in_paragraph(doc, 23, "聯絡電話：", f"聯絡電話：{HOSPITAL_CONTACT_PHONE}")
        _replace_in_paragraph(doc, 24, "乙方：", "乙方：{{store_name}}")
        _replace_in_paragraph(doc, 25, "地址：", "地址：{{address}}")
        _replace_in_paragraph(doc, 26, "負責人：", "負責人：{{owner_name}}")
        _replace_in_paragraph(doc, 27, "統一編號：", "統一編號：{{tax_id}}")
        _replace_in_paragraph(doc, 28, "聯絡人：", "聯絡人：{{contact_person}}")
        _replace_in_paragraph(doc, 29, "聯絡電話：", "聯絡電話：{{phone}}")
        _replace_sequence_in_paragraph(doc, 32, [
            ("年", "{{sign_y}}年"), ("月", "{{sign_m}}月"), ("日", "{{sign_d}}日"),
        ])

        # 段落 7~10 是原本紙本表單留給人工手寫優惠內容的空白行，現在優惠內容已經自動
        # 套版（且本身就能多行條列呈現，見上面段落 6 的調整），這幾行變成多餘的空白，
        # 條列項目多或店名較長時會把版面往下擠、導致簽約日期被擠到自己獨占一頁
        # （2026-09-11 依實際套版結果調整）。刪除必須放在所有段落編號操作的最後
        # 一步——刪除會讓後面的段落編號往前移，先做的話會打亂上面幾行的段落編號。
        start = doc.Paragraphs(7).Range.Start
        end = doc.Paragraphs(10).Range.End
        doc.Range(start, end).Delete()

        doc.SaveAs2(OUTPUT_PATH, FileFormat=WD_FORMAT_DOCUMENT)
        print(f"已產生範本：{OUTPUT_PATH}")
    finally:
        doc.Close(False)
        word.Quit()


if __name__ == "__main__":
    main()
