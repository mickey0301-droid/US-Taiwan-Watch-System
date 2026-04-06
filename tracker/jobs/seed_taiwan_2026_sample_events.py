from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService


EVENT_GROUPS = [
    {
        "slug": "senate_delegation_visit_taiwan",
        "title": "美國聯邦參議院跨黨派訪團訪台",
        "excerpt": "美國聯邦參議員 Jeanne Shaheen、John Curtis、Thom Tillis 與 Jacky Rosen 於 2026 年 3 月底訪問台灣，並就台美夥伴關係、區域安全與經貿合作交換意見。",
        "date_published": "2026-03-30T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Jeanne Shaheen"},
            {"name": "John Curtis"},
            {"name": "Thom Tillis"},
            {"name": "Jacky Rosen"},
        ],
        "sources": [
            {
                "source_url": "https://www.president.gov.tw/News/39935",
                "source_type": "official",
                "source_title": "總統接見美國聯邦參議院跨黨派訪團",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=121991",
                "source_type": "official",
                "source_title": "外交部歡迎美國聯邦參議院跨黨派訪團訪台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "casey_mace_taiwan_visit",
        "title": "美國 APEC 資深官員 Casey Mace 訪台",
        "excerpt": "美國 APEC 資深官員 Casey Mace 於 2026 年 3 月訪問台灣，並與台灣官員就 APEC 參與及區域經貿合作交換意見。",
        "date_published": "2026-03-02T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Casey Mace", "chinese_aliases": ["梅斯"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=96&s=121803",
                "source_type": "official",
                "source_title": "外交部誠摯歡迎美國「亞太經濟合作」資深官員梅斯來台訪問",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202603020118.aspx",
                "source_type": "media",
                "source_title": "美國 APEC 高階官員 Casey Mace 訪台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202603030358.aspx",
                "source_type": "media",
                "source_title": "Casey Mace 拜會外交部、挺台參與 APEC",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "stanley_brown_taiwan_arms_sales_hearing",
        "title": "美國官員在國會聽證會談對台軍售與六項保證",
        "excerpt": "美國國務院政治軍事事務局資深官員 Stanley L. Brown、戰爭部採購與維護次長 Michael Duffey 及國防安全合作署署長 Michael F. Miller 於 2026 年 3 月出席聯邦眾議院外交委員會聽證會，回應對台軍售交付、六項保證與美方對台政策等議題。",
        "date_published": "2026-03-18T00:00:00",
        "statement_type": "hearing",
        "participants": [
            {"name": "Stanley L. Brown", "chinese_aliases": ["布朗"]},
            {"name": "Michael Duffey", "chinese_aliases": ["杜飛"]},
            {"name": "Michael F. Miller", "chinese_aliases": ["米勒"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202603180010.aspx",
                "source_type": "media",
                "source_title": "美官員：未察覺六項保證有變　加速執行對台軍售交付",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "taiwan_us_trade_agreement_federal_officials",
        "title": "美國聯邦官員見證台美對等貿易協定簽署",
        "excerpt": "台美於 2026 年 2 月簽署對等貿易協定，美國貿易代表 Jamieson Greer 與美國商務部長 Howard Lutnick 參與見證，事件也凸顯台美經貿合作與供應鏈夥伴關係。",
        "date_published": "2026-02-14T00:00:00",
        "statement_type": "event",
        "participants": [
            {"name": "Jamieson Greer", "chinese_aliases": ["葛里爾"]},
            {"name": "Howard Lutnick", "chinese_aliases": ["盧特尼克"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602140048.aspx",
                "source_type": "media",
                "source_title": "台美簽署貿易協定　美國駐中使領館轉發台美官員合照",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_secretary_representative_statement",
        "title": "美國務院稱 Raymond Greene 為國務卿在台代表",
        "excerpt": "美國國務院於 2026 年 2 月表示，美國在台協會台北辦事處長 Raymond Greene 是國務卿在台代表，充分代表美國政府對台安全與政策立場。",
        "date_published": "2026-02-09T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602090026.aspx",
                "source_type": "media",
                "source_title": "美國務院：谷立言為國務卿在台代表　充分代表美政府",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_strengthen_partnership_2026",
        "title": "Raymond Greene 會晤林佳龍並討論強化台美關係",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2026 年 1 月與外交部長林佳龍會晤，討論如何進一步強化堅若磐石的台美夥伴關係，並推動外交與經濟領域新倡議。",
        "date_published": "2026-01-08T00:00:00",
        "statement_type": "meeting",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202601080376.aspx",
                "source_type": "media",
                "source_title": "谷立言會晤林佳龍　討論強化台美關係",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_defense_budget_process_2026",
        "title": "Raymond Greene 表示期待台灣就國防預算進行辯論",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2026 年 2 月遭批評介入台灣內政後，美方回應尊重台灣政治程序，並期待立法院就支持台灣建立有效自我防衛所需能力進行辯論。",
        "date_published": "2026-02-04T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602040161.aspx",
                "source_type": "media",
                "source_title": "黃國昌指谷立言介入內政　AIT：尊重台灣政治程序",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_lantern_festival_2026",
        "title": "Raymond Greene 談台北燈節與台美夥伴關係",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2026 年 2 月接受專訪，談及 AIT 首度參與台北燈節、慶祝美國建國 250 年，並提及對台灣國防預算與台美夥伴關係的看法。",
        "date_published": "2026-02-23T00:00:00",
        "statement_type": "interview",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602230186.aspx",
                "source_type": "media",
                "source_title": "AIT首度參與台北燈節　谷立言：同慶美國250歲",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_new_year_market_2026",
        "title": "Raymond Greene 市場拜年談美台友好",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2026 年農曆春節前走訪市場辦年貨，並透過 AIT 官方社群傳達美台友好與台美夥伴關係持續深化的訊息。",
        "date_published": "2026-02-15T00:00:00",
        "statement_type": "event",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602150096.aspx",
                "source_type": "media",
                "source_title": "台灣女婿谷立言市場辦年貨　「美台友好」壽桃吸睛",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "donald_trump_taiwan_supply_chain_shift_2026",
        "title": "Donald Trump 政府推動台灣半導體供應鏈赴美擴產",
        "excerpt": "美國商務部長 Howard Lutnick 於 2026 年 1 月表示，川普政府目標是在 Donald Trump 任內將台灣半導體供應鏈相當部分產能移往美國，以強化美國製造與經濟安全。",
        "date_published": "2026-01-16T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
            {"name": "Howard Lutnick", "chinese_aliases": ["盧特尼克"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202601160016.aspx",
                "source_type": "media",
                "source_title": "盧特尼克：川普任內目標將台灣半導體產能40%轉移至美",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "donald_trump_taiwan_defense_appropriations_2026",
        "title": "Donald Trump 簽署含對台防衛經費的綜合撥款法案",
        "excerpt": "美國總統 Donald Trump 於 2026 年 2 月簽署綜合撥款法案，其中包含逾 14 億美元支持台灣防衛與安全合作相關經費。",
        "date_published": "2026-02-06T00:00:00",
        "statement_type": "law_signing",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602060038.aspx",
                "source_type": "media",
                "source_title": "川普簽署綜合撥款法案　含逾14億美元支持台灣防衛",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "donald_trump_japan_summit_taiwan_2026",
        "title": "Donald Trump 與日方峰會重申台海和平穩定重要性",
        "excerpt": "美國總統 Donald Trump 於 2026 年 3 月與日本首相舉行峰會，美方事實清單重申維護台海和平與穩定的重要性，並反對任何以武力或脅迫片面改變現狀的企圖。",
        "date_published": "2026-03-20T00:00:00",
        "statement_type": "summit",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=121932",
                "source_type": "official",
                "source_title": "外交部歡迎美方發布「美日峰會事實清單」，重申台海和平穩定的重要性",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "donald_trump_taiwan_chip_expansion_2026",
        "title": "Donald Trump 談台灣晶片與美國擴產",
        "excerpt": "美國總統 Donald Trump 於 2026 年 2 月被報導提及與中國領導人談台灣議題時的態度，並敦促美國加速推動晶片在美生產，以降低對台灣高階晶片供應的依賴。",
        "date_published": "2026-02-26T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Donald Trump", "chinese_aliases": ["川普", "美國總統川普"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602260010.aspx",
                "source_type": "media",
                "source_title": "紐時：川普指習近平談台灣「呼吸沉重」　加速美國晶片擴產腳步",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "jacob_helberg_pax_silica_2026",
        "title": "Jacob Helberg 談台灣參與矽和平供應鏈合作",
        "excerpt": "美國國務院經濟事務次卿 Jacob Helberg 於 2026 年 1 月表示，台灣是矽和平成員之一，強調可信賴夥伴在 AI 與關鍵供應鏈合作中的重要性。",
        "date_published": "2026-01-30T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Jacob Helberg", "chinese_aliases": ["海柏格"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202601300038.aspx",
                "source_type": "media",
                "source_title": "美「矽和平」納台灣　次卿海柏格：成員可信賴不受他國收買",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "taiwan_defense_budget_letter",
        "title": "美國跨黨派議員呼籲台灣提高國防支出",
        "excerpt": "美國跨黨派議員於 2026 年 2 月聯名呼籲台灣提高國防支出，以嚇阻中國擴張並強化台美安全合作。",
        "date_published": "2026-02-18T00:00:00",
        "statement_type": "letter",
        "participants": [
            {"name": "Pete Ricketts", "chinese_aliases": ["芮基茲"]},
            {"name": "Chris Coons", "chinese_aliases": ["昆斯"]},
            {"name": "Young Kim", "chinese_aliases": ["金映玉"]},
            {"name": "Ami Bera", "chinese_aliases": ["貝拉"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602130090.aspx",
                "source_type": "media",
                "source_title": "美國 34 名跨黨派議員致函 支持台灣國防特別預算",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "pete_ricketts_budget_commitment_2026",
        "title": "Pete Ricketts 強調台灣須落實國防預算承諾",
        "excerpt": "美國參議員 Pete Ricketts 於 2026 年 2 月表示，台灣必須落實強而有力的國防預算承諾，展現捍衛民主與自我防衛的決心。",
        "date_published": "2026-02-18T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Pete Ricketts", "chinese_aliases": ["芮基茲"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602180033.aspx",
                "source_type": "media",
                "source_title": "立院開議先審國防預算　美議員：台灣須落實承諾",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "taiwan_presidential_elections_resolution",
        "title": "美國跨黨派參議員肯定台灣總統直選 30 週年",
        "excerpt": "美國跨黨派參議員於 2026 年 3 月提出決議案，肯定台灣總統直選 30 週年並重申支持台灣民主與安全。",
        "date_published": "2026-03-24T00:00:00",
        "statement_type": "resolution",
        "participants": [
            {"name": "Tammy Duckworth"},
            {"name": "John Curtis"},
            {"name": "Tim Kaine"},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=96&s=121949",
                "source_type": "official",
                "source_title": "外交部感謝美國聯邦參議院跨黨派議員提出決議案",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "moolenaar_trade_agreement_statement",
        "title": "美國議員 John Moolenaar 肯定台美持續夥伴關係",
        "excerpt": "美國聯邦眾議員 John Moolenaar 於 2026 年 2 月表示，台美新貿易協定展現雙方持續夥伴關係，並強調台灣在關鍵供應鏈的重要性。",
        "date_published": "2026-02-15T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "John Moolenaar", "chinese_aliases": ["穆勒納爾"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602150012.aspx",
                "source_type": "media",
                "source_title": "台美簽署貿易協定 美議員：展現雙方持續夥伴關係",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "indiana_resolution_supports_taiwan",
        "title": "美國印第安納州通過友台決議",
        "excerpt": "美國印第安納州州參眾兩院於 2026 年 2 月通過友台決議，支持台美貿易談判、台灣關係法及台灣保證實施法，並鼓勵官員在不受威脅下訪台。",
        "date_published": "2026-02-25T00:00:00",
        "statement_type": "resolution",
        "participants": [],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602250073.aspx",
                "source_type": "media",
                "source_title": "美國中西部州通過友台決議 挺官員不受威脅訪台",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "us_young_leaders_visit_taiwan",
        "title": "美國青年領袖訪問團訪台",
        "excerpt": "美國青年領袖訪問團於 2026 年 3 月訪問台灣，就台美青年參政、教育合作、各州交流與區域情勢交換意見，團長為密西根州州眾議員 Jason Morgan。",
        "date_published": "2026-03-24T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Jason Morgan", "chinese_aliases": ["摩根"]},
        ],
        "sources": [
            {
                "source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=121952",
                "source_type": "official",
                "source_title": "外交部誠摯歡迎美國青年領袖訪問團訪問台灣",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": True,
            },
        ],
    },
    {
        "slug": "jefferson_shreve_taiwan_visit_plan",
        "title": "美國眾議員 Jefferson Shreve 表示將訪台聚焦安全議題",
        "excerpt": "美國聯邦眾議員 Jefferson Shreve 於 2026 年 3 月表示，預計 4 月初訪問台灣，並將聚焦安全議題與台美戰略合作。",
        "date_published": "2026-03-19T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Jefferson Shreve", "chinese_aliases": ["希里夫"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202603190066.aspx",
                "source_type": "media",
                "source_title": "美共和黨籍眾議員擬 4 月初訪台 聚焦安全議題",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "ruben_gallego_taiwan_defense_statement",
        "title": "美國參議員 Ruben Gallego 關切台灣國防預算",
        "excerpt": "美國聯邦參議員 Ruben Gallego 於 2026 年 2 月表示，現在絕非削弱台灣國防的時候，並關切台灣立法院對國防特別條例的審議方向。",
        "date_published": "2026-02-04T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Ruben Gallego", "chinese_aliases": ["加勒戈"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aopl/202602040039.aspx",
                "source_type": "media",
                "source_title": "立院付委在野國防預算 美議員：現在非削弱國防時機",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "ted_cruz_taiwan_symbols_act_statement",
        "title": "美國參院外委會審議台灣主權象徵法案",
        "excerpt": "美國聯邦參議院外交委員會於 2026 年 1 月審議台灣主權象徵法案，提案議員 Ted Cruz 爭取支持，Tammy Duckworth、Jeff Merkley、Jeanne Shaheen 等議員也表達各自立場。",
        "date_published": "2026-01-30T00:00:00",
        "statement_type": "hearing",
        "participants": [
            {"name": "Ted Cruz", "chinese_aliases": ["克魯茲"]},
            {"name": "Tammy Duckworth"},
            {"name": "Jeff Merkley"},
            {"name": "Jeanne Shaheen"},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202601300017.aspx",
                "source_type": "media",
                "source_title": "美參院外委會審台灣主權法案 議員各持立場最終擱置",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_freedom_not_free_2026",
        "title": "Raymond Greene 提自由不是免費並談台灣國防投資",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2026 年 1 月出席國防安全研究院活動時，以攜手投資共創安全繁榮未來為題發表演講，並提及自由不是免費，強調台灣國防投資與安全繁榮的連動。",
        "date_published": "2026-01-22T00:00:00",
        "statement_type": "speech",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202601220405.aspx",
                "source_type": "media",
                "source_title": "谷立言提自由不是免費 總統府籲立院速審國防條例",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "raymond_greene_secure_supply_chain_2026",
        "title": "Raymond Greene 表示台灣在安全供應鏈扮演重要角色",
        "excerpt": "美國在台協會處長 Raymond Greene 於 2026 年 2 月赴台中參訪機械產業時表示，台灣是非常可靠、可信賴的夥伴，在安全供應鏈上扮演重要角色。",
        "date_published": "2026-02-06T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Raymond Greene", "chinese_aliases": ["谷立言"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602060059.aspx",
                "source_type": "media",
                "source_title": "訪機械產業 谷立言：台灣扮演安全供應鏈重要角色",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
]


def _resolve_person_ids(officials_service: OfficialsService, participants: list[dict]) -> list[int]:
    person_ids: list[int] = []
    for participant in participants:
        name = participant["name"]
        person = officials_service.find_person(name)
        if not person:
            person, _ = officials_service.upsert_person(
                {
                    "full_name": name,
                    "source_url": participant.get("source_url") or participant.get("seed_source_url") or "https://www.cna.com.tw/",
                    "source_type": participant.get("source_type") or "media",
                    "seed_source_type": participant.get("source_type") or "media",
                    "profile_status": "seeded",
                    "verification_status": "unverified",
                    "raw_payload": {
                        "manual_seed": True,
                        "seed_context": "taiwan_2026_event_seed",
                    },
                }
            )
        for alias in participant.get("chinese_aliases", []):
            officials_service.ensure_alias(
                person.id,
                alias,
                source_url=participant.get("source_url"),
                source_type=participant.get("source_type") or "media",
                alias_type="chinese_name",
            )
        if person.id not in person_ids:
            person_ids.append(person.id)
    return person_ids


def run_seed_taiwan_2026_sample_events() -> dict:
    with session_scope() as session:
        officials_service = OfficialsService(session)
        statements_service = StatementsService(session)
        sync_run = SyncRun(
            job_name="seed_taiwan_2026_sample_events",
            job_type="statement_seed",
            source_name="president_mofa_cna_manual_seed",
        )
        session.add(sync_run)
        session.flush()

        events_processed = 0
        sources_processed = 0
        created_count = 0
        updated_count = 0
        phase_results: list[dict[str, object]] = []

        for index, event in enumerate(EVENT_GROUPS, start=1):
            participant_ids = _resolve_person_ids(officials_service, event["participants"])
            lead_person_id = participant_ids[0] if participant_ids else None
            event_created = False

            for source in event["sources"]:
                payload = {
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
                        "seeded_from": "manual_taiwan_2026_sources",
                        "participant_names": [item["name"] for item in event["participants"]],
                    },
                }
                _, created = statements_service.ingest_statement(payload)
                sources_processed += 1
                if created:
                    created_count += 1
                    event_created = True
                else:
                    updated_count += 1

            events_processed += 1
            phase_label = "first_three" if index <= 3 else "remaining"
            phase_results.append(
                {
                    "phase": phase_label,
                    "event_slug": event["slug"],
                    "participant_count": len(participant_ids),
                    "sources_attached": len(event["sources"]),
                    "created_event": event_created,
                }
            )

        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "success"
        sync_run.records_found = events_processed
        sync_run.records_created = created_count
        sync_run.records_updated = updated_count
        sync_run.meta = {
            "sources_processed": sources_processed,
            "phases": phase_results,
        }
        return {
            "status": "success",
            "job_name": "seed_taiwan_2026_sample_events",
            "events_processed": events_processed,
            "records_created": created_count,
            "records_updated": updated_count,
            "sources_processed": sources_processed,
            "phases": phase_results,
        }
