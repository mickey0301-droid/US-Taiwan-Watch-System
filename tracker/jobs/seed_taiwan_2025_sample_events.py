from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService


EVENT_GROUPS = [
    {
        "slug": "senate_bipartisan_delegation_visit_taiwan_2025",
        "title": "美國聯邦參議院跨黨派訪團訪台",
        "excerpt": "美國聯邦參議員 Pete Ricketts、Chris Coons 與 Ted Budd 於 2025 年 4 月訪問台灣，與台灣方面就台美夥伴關係、區域安全與經貿合作交換意見。",
        "date_published": "2025-04-16T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Pete Ricketts", "chinese_aliases": ["芮基茲"]},
            {"name": "Chris Coons", "chinese_aliases": ["昆斯"]},
            {"name": "Ted Budd", "chinese_aliases": ["巴德"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=119646",
                "source_type": "official",
                "source_title": "外交部歡迎美國聯邦參議院跨黨派訪團訪台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "natural_resources_committee_visit_taiwan_2025",
        "title": "美國聯邦眾議院天然資源委員會主席率跨黨派訪團訪台",
        "excerpt": "美國聯邦眾議院天然資源委員會主席 Bruce Westerman 於 2025 年 5 月率 Sarah Elfreth、Harriet Hageman、Celeste Maloy 與 Nick Begich 等跨黨派議員訪問台灣。",
        "date_published": "2025-05-27T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Bruce Westerman", "chinese_aliases": ["魏德曼"]},
            {"name": "Sarah Elfreth", "chinese_aliases": ["艾佛瑞斯"]},
            {"name": "Harriet Hageman", "chinese_aliases": ["海吉曼"]},
            {"name": "Celeste Maloy", "chinese_aliases": ["馬洛伊"]},
            {"name": "Nick Begich", "chinese_aliases": ["貝吉其"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=119957",
                "source_type": "official",
                "source_title": "外交部歡迎美國聯邦眾議院天然資源委員會主席率跨黨派訪團訪台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202505270210.aspx",
                "source_type": "media",
                "source_title": "美國跨黨派議員團訪台 AIT：致力強化與台夥伴關係",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "ami_bera_democratic_delegation_visit_taiwan_2025",
        "title": "美國國會台灣連線共同主席 Ami Bera 率民主黨議員團訪台",
        "excerpt": "美國國會台灣連線共同主席 Ami Bera 於 2025 年 6 月率 Gabe Amo、Wesley Bell、Julie Johnson、Sarah McBride 與 Johnny Olszewski 等民主黨議員訪問台灣。",
        "date_published": "2025-06-16T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Ami Bera", "chinese_aliases": ["貝拉"]},
            {"name": "Gabe Amo", "chinese_aliases": ["阿莫"]},
            {"name": "Wesley Bell", "chinese_aliases": ["貝爾"]},
            {"name": "Julie Johnson", "chinese_aliases": ["強生"]},
            {"name": "Sarah McBride", "chinese_aliases": ["麥布萊德"]},
            {"name": "Johnny Olszewski", "chinese_aliases": ["奧謝夫斯基"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=120045",
                "source_type": "official",
                "source_title": "外交部歡迎美國聯邦眾議院國會台灣連線共同主席 Ami Bera 率民主黨議員團訪台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "tammy_duckworth_visit_taiwan_2025",
        "title": "總統接見美國聯邦參議員 Tammy Duckworth 訪團",
        "excerpt": "總統於 2025 年 5 月接見美國聯邦參議員 Tammy Duckworth 訪團，雙方就台美安全合作、供應鏈韌性與區域情勢交換意見。",
        "date_published": "2025-05-28T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Tammy Duckworth"},
        ],
        "sources": [
            {
                "source_url": "https://www.president.gov.tw/News/39264",
                "source_type": "official",
                "source_title": "總統接見美國聯邦參議員 Tammy Duckworth 訪團",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "state_department_taiwan_contact_statement_2025",
        "title": "美國國務院談美方官員訪台與台美互動",
        "excerpt": "美國國務院於 2025 年 4 月表示，美台維持密切但非官方關係，並指出雙方有持續擴大接觸與互動的動力。",
        "date_published": "2025-04-22T00:00:00",
        "statement_type": "statement",
        "participants": [],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202504220016.aspx",
                "source_type": "media",
                "source_title": "美國務院：美官員可能訪台且有擴大接觸動力",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "marco_rubio_state_department_reorganization_2025",
        "title": "Marco Rubio 公布國務院改組藍圖並保留涉台架構",
        "excerpt": "美國國務卿 Marco Rubio 於 2025 年 4 月公布外交機構改組藍圖，報導指出東亞暨太平洋事務局等涉台架構仍予保留，並新增聚焦網路安全與人工智慧的新興威脅助卿職位。",
        "date_published": "2025-04-23T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202504230031.aspx",
                "source_type": "media",
                "source_title": "美國務院改組涉台架構不變 擬增「新興威脅助卿」",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "marco_rubio_taiwan_deterrence_statement_2025",
        "title": "Marco Rubio 表示台灣自衛能力是嚇阻關鍵",
        "excerpt": "美國國務卿 Marco Rubio 於 2025 年 5 月在參議院聽證會表示，建構嚇阻先從台灣自衛能力開始，台灣愈難被攻下，愈能爭取更多時間。",
        "date_published": "2025-05-21T00:00:00",
        "statement_type": "hearing",
        "participants": [
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202505210009.aspx",
                "source_type": "media",
                "source_title": "盧比歐：台自衛能力成嚇阻關鍵 愈難攻愈能爭取時間",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "marco_rubio_support_commitment_2025",
        "title": "Marco Rubio 重申美國反對武力改變台海現狀",
        "excerpt": "美國國務卿 Marco Rubio 於 2025 年 2 月接受專訪時重申，美國依據《台灣關係法》及六項保證支持台灣，反對任何透過武力、威脅或脅迫改變現狀的作法，並支持台灣國際參與。",
        "date_published": "2025-02-22T00:00:00",
        "statement_type": "interview",
        "participants": [
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=97&s=119171",
                "source_type": "official",
                "source_title": "有關美國國務卿盧比歐接受專訪時重申美方對台承諾",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "marco_rubio_no_tradeoff_taiwan_2025",
        "title": "Marco Rubio 表示不會以放棄台灣交換美中協議",
        "excerpt": "美國國務卿 Marco Rubio 於 2025 年 10 月回應媒體表示，美國不會為達成與中國的貿易協定而放棄台灣，並重申台海和平穩定的重要性。",
        "date_published": "2025-10-26T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=120952",
                "source_type": "official",
                "source_title": "有關美國國務卿盧比歐回應媒體時重申美國長期以來對台灣之支持事，外交部回應如下",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "donald_trump_taiwan_assurance_act_2025",
        "title": "Donald Trump 簽署台灣保證實施法案",
        "excerpt": "美國總統 Donald Trump 於 2025 年 12 月正式簽署台灣保證實施法案，要求美國國務院定期檢視並更新與台灣的交往準則，並提出解除自我限制的落實計畫。",
        "date_published": "2025-12-03T00:00:00",
        "statement_type": "law_signing",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512030032.aspx",
                "source_type": "media",
                "source_title": "川普簽署台灣保證實施法案　研擬解除美台交往限制",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512030074.aspx",
                "source_type": "media",
                "source_title": "川普簽署台灣保證實施法案　府：持續深化台美關係",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512040110.aspx",
                "source_type": "media",
                "source_title": "川普簽台灣保證實施法案　分析：時機重於實質內容",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "donald_trump_nss_taiwan_2025",
        "title": "Donald Trump 政府國安戰略報告強調台灣重要性",
        "excerpt": "白宮於 2025 年 12 月公布 Donald Trump 任內首份國家安全戰略報告，涉台措辭較過往更強硬，指出嚇阻台海衝突是優先要務，並多次提及台灣戰略重要性。",
        "date_published": "2025-12-06T00:00:00",
        "statement_type": "policy_report",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512060068.aspx",
                "source_type": "media",
                "source_title": "川普2025國安戰略報告提台灣8次　路透：措辭更強硬",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512060019.aspx",
                "source_type": "media",
                "source_title": "川普2.0國安戰略報告　專家：正確點出台灣戰略重要",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "donald_trump_ndaa_taiwan_2025",
        "title": "Donald Trump 簽署含友台條文的國防授權法案",
        "excerpt": "美國總統 Donald Trump 於 2025 年 12 月簽署 2026 財政年度國防授權法案，內容包含台灣安全合作倡議、海巡訓練與無人系統合作等多項友台條文。",
        "date_published": "2025-12-19T00:00:00",
        "statement_type": "law_signing",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512190038.aspx",
                "source_type": "media",
                "source_title": "川普簽署國防授權法　含軍援台灣最高10億美元",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512190222.aspx",
                "source_type": "media",
                "source_title": "川普簽署國防授權法　林佳龍感謝美跨黨派堅定挺台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512190280.aspx",
                "source_type": "media",
                "source_title": "川普簽署國防授權法案　國防部：誠摯感謝美方友台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=121351",
                "source_type": "official",
                "source_title": "外交部對美國總統川普簽署《2026會計年度國防授權法案》表達歡迎之意",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "jd_vance_ai_chip_statement_2025",
        "title": "JD Vance 談 AI 晶片在美製造與台灣半導體",
        "excerpt": "美國副總統 JD Vance 於 2025 年 2 月在巴黎 AI 行動高峰會表示，川普政府將確保最強大的 AI 系統在美國建造，並使用美國設計與製造的晶片；相關報導提及台灣半導體與美方關稅政策。",
        "date_published": "2025-02-12T00:00:00",
        "statement_type": "speech",
        "participants": [
            {"name": "JD Vance", "chinese_aliases": ["范斯", "美國副總統范斯"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aopl/202502120015.aspx",
                "source_type": "media",
                "source_title": "美副總統范斯：美國政府將確保AI晶片在美國製造",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "jacob_helberg_supply_chain_statement_2025",
        "title": "Jacob Helberg 表示台灣是供應鏈關鍵角色",
        "excerpt": "美國國務院經濟事務次卿 Jacob Helberg 於 2025 年 12 月表示，台灣在供應鏈中具關鍵地位，並提到台美經濟繁榮夥伴對話預計於隔年初再度舉行。",
        "date_published": "2025-12-17T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Jacob Helberg", "chinese_aliases": ["海柏格"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512170003.aspx",
                "source_type": "media",
                "source_title": "美官員：台灣是供應鏈要角　經濟夥伴對話明年初舉行",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "jacob_helberg_pax_silica_followup_2025",
        "title": "Jacob Helberg 再談台灣參與矽和平峰會",
        "excerpt": "美國國務院經濟事務次卿 Jacob Helberg 於 2025 年 12 月表示，台灣參與矽和平峰會能為供應鏈合作做出寶貴貢獻，並強調可信賴夥伴的重要性。",
        "date_published": "2025-12-18T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Jacob Helberg", "chinese_aliases": ["海柏格"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512180007.aspx",
                "source_type": "media",
                "source_title": "美次卿：台灣參與矽和平峰會貢獻寶貴　共推供應鏈合作",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "roger_wicker_deb_fischer_visit_taiwan_2025",
        "title": "美國聯邦參議院軍事委員會主席率團訪台",
        "excerpt": "美國聯邦參議院軍事委員會主席 Roger Wicker 於 2025 年 8 月率 Deb Fischer 等國會成員訪問台灣，與台灣方面就區域安全、台美合作及自我防衛能力交換意見。",
        "date_published": "2025-08-29T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Roger Wicker", "chinese_aliases": ["維克"]},
            {"name": "Deb Fischer", "chinese_aliases": ["費雪"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=120568",
                "source_type": "official",
                "source_title": "外交部誠摯歡迎美國聯邦參議院軍事委員會主席維克率團訪問台灣",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "roger_wicker_taiwan_budget_support_2025",
        "title": "Roger Wicker 肯定台灣提高國防預算決心",
        "excerpt": "美國參議院軍事委員會主席 Roger Wicker 於 2025 年 11 月表示，台灣提出國防特別預算展現自我防衛決心，相關作為有助於強化嚇阻與安全合作。",
        "date_published": "2025-11-26T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Roger Wicker", "chinese_aliases": ["維克"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202511260212.aspx",
                "source_type": "media",
                "source_title": "台灣提兆元國防特別預算　美議員：展現自我防衛決心",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_45_year_peace_2025",
        "title": "Raymond Greene 談深化台美夥伴關係延續和平",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 5 月表示，美台關係建立在深厚經濟連結與共享利益之上，透過供應鏈與投資合作可讓過去 45 年守護的和平延續下去。",
        "date_published": "2025-05-24T00:00:00",
        "statement_type": "speech",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202505240076.aspx",
                "source_type": "media",
                "source_title": "谷立言：深化美台夥伴關係 讓和平延續下一個45年",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_tsmc_investment_2025",
        "title": "Raymond Greene 表示台積電投資強化台美繁榮與安全",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 3 月表示，台積電擴大對美投資不僅強化台美雙方的經濟繁榮與安全，也展現兩個科技與經濟強權的深厚連結。",
        "date_published": "2025-03-04T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202503040317.aspx",
                "source_type": "media",
                "source_title": "台積電擴大對美投資 谷立言：強化雙方經濟繁榮安全",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_commitment_unchanged_2025",
        "title": "Raymond Greene 表示美對台承諾不變",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 3 月表示，美國不會放棄台灣，對台承諾從未改變，並指出 Donald Trump 也多次強調不希望看到台海衝突發生。",
        "date_published": "2025-03-12T00:00:00",
        "statement_type": "interview",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202503120406.aspx",
                "source_type": "media",
                "source_title": "谷立言：美對台承諾不變 不希望見到片面改變台海現狀",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_trump_allies_contribution_2025",
        "title": "Raymond Greene 解讀 Donald Trump 對盟友與台海政策",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 3 月表示，Donald Trump 政府的重要原則是盟友須有貢獻以共同維護和平，並強調印太與維持台海現狀是美方聚焦重點。",
        "date_published": "2025-03-18T00:00:00",
        "statement_type": "interview",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202503180305.aspx",
                "source_type": "media",
                "source_title": "谷立言：川普原則是盟友須有貢獻 共同維護和平",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_gctf_joint_statement_2025",
        "title": "Raymond Greene 參與 GCTF 十週年聯合聲明",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 5 月與外交部及多國夥伴共同發表 GCTF 十週年聯合聲明，強調持續深化夥伴關係與國際合作。",
        "date_published": "2025-05-27T00:00:00",
        "statement_type": "joint_statement",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=96&s=119956",
                "source_type": "official",
                "source_title": "「全球合作暨訓練架構」成立十周年，外交部長林佳龍與夥伴國發表聯合聲明",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "raymond_greene_trade_golden_age_2025",
        "title": "Raymond Greene 稱美台經貿邁入黃金時代",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 11 月在台灣美國商會活動上表示，美台經貿關係將邁入黃金時代，台海和平與可信賴供應鏈對雙邊合作至關重要。",
        "date_published": "2025-11-18T00:00:00",
        "statement_type": "speech",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202511180349.aspx",
                "source_type": "media",
                "source_title": "谷立言：美台經貿邁入黃金時代　對雙邊關係非常樂觀",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_defense_budget_indispensable_2025",
        "title": "Raymond Greene 歡迎台灣追加國防預算",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 11 月表示，台灣提出 1.25 兆元追加國防預算，對嚇阻威脅全球和平與繁榮的挑戰不可或缺。",
        "date_published": "2025-11-26T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202511260132.aspx",
                "source_type": "media",
                "source_title": "谷立言：1.25兆元國防特別預算　對嚇阻挑戰不可或缺",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_ivlp_2025",
        "title": "Raymond Greene 於 IVLP 85 週年談全球夥伴關係",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 12 月在 IVLP 85 週年茶會表示，這項計畫是美國國務院旗艦交流平台，與世界各地領袖共築歷久不衰的夥伴關係。",
        "date_published": "2025-12-08T00:00:00",
        "statement_type": "speech",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512080236.aspx",
                "source_type": "media",
                "source_title": "IVLP85週年　谷立言：與世界各地領袖共築夥伴關係",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_defense_budget_unity_2025",
        "title": "Raymond Greene 盼台灣各黨支持國防預算",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2025 年 12 月表示，台灣追加國防預算是透過嚇阻提升和平的重要一步，並期盼台灣各政黨能團結一致支持相關支出。",
        "date_published": "2025-12-10T00:00:00",
        "statement_type": "interview",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202512100375.aspx",
                "source_type": "media",
                "source_title": "總統提1.25兆國防預算　谷立言盼台灣各黨團結一致",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "ncsl_delegation_visit_taiwan_2025",
        "title": "美國全美州議會聯合會訪問團訪台",
        "excerpt": "美國全美州議會聯合會訪問團於 2025 年 12 月訪問台灣，與台灣方面就台美及兩岸關係、經貿能源、高科技產業合作、人才培育、醫療保健與地方層級交流交換意見。",
        "date_published": "2025-12-04T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Marcus Evans", "chinese_aliases": ["伊凡斯"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=96&s=121268&sms=74",
                "source_type": "official",
                "source_title": "外交部誠摯歡迎美國全美州議會聯合會訪問團訪問台灣",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "donald_trump_semiconductor_tariff_taiwan_2025",
        "title": "Donald Trump 談台灣晶片與半導體關稅",
        "excerpt": "美國總統 Donald Trump 於 2025 年 1 月表示，美國很快會對電腦晶片與半導體課徵關稅，並稱晶片製造業都跑去台灣，希望相關產業回到美國。",
        "date_published": "2025-01-28T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202501280160.aspx",
                "source_type": "media",
                "source_title": "川普稱對半導體課關稅 總統府：台美互助合作關係緊密",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "donald_trump_taiwan_is_taiwan_2025",
        "title": "Donald Trump 表示台灣就是台灣",
        "excerpt": "美國總統 Donald Trump 於 2025 年 10 月在亞洲行期間表示，不確定是否會和中國國家主席習近平談到台灣議題，並稱台灣就是台灣。",
        "date_published": "2025-10-29T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202510290111.aspx",
                "source_type": "media",
                "source_title": "川普：台灣就是台灣 不確定是否和習近平談兩岸議題",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "donald_trump_chip_workers_taiwan_2025",
        "title": "Donald Trump 再談台灣與晶片設廠",
        "excerpt": "美國總統 Donald Trump 於 2025 年 11 月再度提及台灣與晶片產業，表示美國需要外籍技術人員協助赴美設廠與運作，並稱不責怪台灣取得晶片產業優勢。",
        "date_published": "2025-11-20T00:00:00",
        "statement_type": "speech",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202511200014.aspx",
                "source_type": "media",
                "source_title": "川普再提台灣和晶片業：美國需要外籍人員助設廠運作",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "marco_rubio_international_forums_taiwan_2025",
        "title": "Marco Rubio 表示全力推動台灣參與國際論壇",
        "excerpt": "美國準國務卿 Marco Rubio 於 2025 年 1 月出席參院人事任命聽證會時表示，必須找到一切可能的機會，讓台灣參與討論重要議題、但未被代表的國際論壇。",
        "date_published": "2025-01-16T00:00:00",
        "statement_type": "hearing",
        "participants": [
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
            {"name": "John Curtis", "chinese_aliases": ["柯蒂斯"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202501160017.aspx",
                "source_type": "media",
                "source_title": "美準國務卿盧比歐：全力推動讓台灣參與國際論壇",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "marco_rubio_democratic_country_taiwan_2025",
        "title": "Marco Rubio 稱台灣是民主國家",
        "excerpt": "美國國務卿 Marco Rubio 於 2025 年 2 月在瓜地馬拉與總統阿雷巴洛共同記者會上主動提及台灣，並以民主國家形容台灣，表示美國將致力強化美國、瓜地馬拉與台灣的外交關係。",
        "date_published": "2025-02-06T00:00:00",
        "statement_type": "press_conference",
        "participants": [
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aopl/202502060019.aspx",
                "source_type": "media",
                "source_title": "盧比歐稱台灣民主國家 瓜地馬拉總統承諾深化台瓜關係",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "marco_rubio_apec_friendliness_taiwan_2025",
        "title": "APEC 期間 Marco Rubio 被指高度重視台灣",
        "excerpt": "台灣代表團成員吳誠文於 2025 年 11 月談及在 APEC 期間與美國國務卿 Marco Rubio 會面，表示盧比歐非常熱情，且明顯感受到他對台灣的重視與友善態度。",
        "date_published": "2025-11-08T00:00:00",
        "statement_type": "meeting",
        "participants": [
            {"name": "Marco Rubio", "chinese_aliases": ["盧比歐"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aie/202511080235.aspx",
                "source_type": "media",
                "source_title": "吳誠文談APEC與盧比歐會面 非常熱情且重視台灣",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
]


def _resolve_person_ids(officials_service: OfficialsService, participants: list[dict]) -> list[int]:
    person_ids: list[int] = []
    for participant in participants:
        person = officials_service.find_person(participant["name"])
        if not person:
            person, _ = officials_service.upsert_person(
                {
                    "full_name": participant["name"],
                    "source_url": "https://www.cna.com.tw/",
                    "source_type": "media",
                    "seed_source_type": "media",
                    "profile_status": "seeded",
                    "verification_status": "unverified",
                    "raw_payload": {
                        "manual_seed": True,
                        "seed_context": "taiwan_2025_event_seed",
                    },
                }
            )
        for alias in participant.get("chinese_aliases", []):
            officials_service.ensure_alias(
                person.id,
                alias,
                source_url="https://www.cna.com.tw/",
                source_type="media",
                alias_type="chinese_name",
            )
        if person.id not in person_ids:
            person_ids.append(person.id)
    return person_ids


def run_seed_taiwan_2025_sample_events() -> dict:
    with session_scope() as session:
        officials_service = OfficialsService(session)
        statements_service = StatementsService(session)
        sync_run = SyncRun(
            job_name="seed_taiwan_2025_sample_events",
            job_type="statement_seed",
            source_name="president_mofa_cna_manual_seed",
        )
        session.add(sync_run)
        session.flush()

        events_processed = 0
        created_count = 0
        updated_count = 0
        sources_processed = 0

        for event in EVENT_GROUPS:
            participant_ids = _resolve_person_ids(officials_service, event["participants"])
            lead_person_id = participant_ids[0] if participant_ids else None

            for source in event["sources"]:
                _, created = statements_service.ingest_statement(
                    {
                        "person_id": lead_person_id,
                        "participant_ids": participant_ids,
                        "title": event["title"],
                        "source_title": source["source_title"],
                        "date_published": datetime.fromisoformat(event["date_published"]),
                        "source_url": source["source_url"],
                        "source_type": source["source_type"],
                        "statement_type": event["statement_type"],
                        "excerpt": event["excerpt"],
                        "full_text": event["excerpt"],
                        "raw_text": event["excerpt"],
                        "is_primary_source": source["is_primary_source"],
                        "parser_identity": source["parser_identity"],
                        "raw_payload": {
                            "event_slug": event["slug"],
                            "seeded_from": "manual_taiwan_2025_sources",
                            "participant_names": [item["name"] for item in event["participants"]],
                        },
                    }
                )
                sources_processed += 1
                if created:
                    created_count += 1
                else:
                    updated_count += 1

            events_processed += 1

        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "success"
        sync_run.records_found = events_processed
        sync_run.records_created = created_count
        sync_run.records_updated = updated_count
        sync_run.meta = {"sources_processed": sources_processed}
        return {
            "status": "success",
            "job_name": "seed_taiwan_2025_sample_events",
            "events_processed": events_processed,
            "records_created": created_count,
            "records_updated": updated_count,
            "sources_processed": sources_processed,
        }
