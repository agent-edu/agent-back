import io
import zipfile
import xml.etree.ElementTree as ET

import httpx
from elasticsearch import Elasticsearch
from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings

from app.core.config import settings

# 기업코드 캐시 (서버 기동 중 메모리에 유지, 한국 주식 한글명 → 종목코드 변환용)
_corp_code_cache: list[dict] | None = None

# ES 클라이언트 캐시
_es_client: Elasticsearch | None = None

RESEARCH_INDEX = "naver-research-reports"


def _get_es_client() -> Elasticsearch | None:
    """Elasticsearch 클라이언트를 반환한다 (싱글턴)."""
    global _es_client
    if _es_client is not None:
        return _es_client

    if not settings.ES_URL or not settings.ES_PASSWORD:
        return None

    _es_client = Elasticsearch(
        settings.ES_URL,
        basic_auth=(settings.ES_USER, settings.ES_PASSWORD),
        verify_certs=False,
    )
    return _es_client


async def _load_corp_codes() -> list[dict]:
    """DART corpCode.xml ZIP을 다운로드하여 기업코드 목록을 반환합니다 (캐시 사용)."""
    global _corp_code_cache
    if _corp_code_cache is not None:
        return _corp_code_cache

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                "https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key": settings.DART_API_KEY},
            )
            resp.raise_for_status()

        z = zipfile.ZipFile(io.BytesIO(resp.content))
        xml_name = z.namelist()[0]
        tree = ET.parse(z.open(xml_name))
        root = tree.getroot()

        corps = []
        for item in root.iter("list"):
            corp_code = item.findtext("corp_code", "")
            corp_name = item.findtext("corp_name", "")
            stock_code = item.findtext("stock_code", "")
            if corp_code and corp_name:
                corps.append({
                    "corp_code": corp_code,
                    "corp_name": corp_name,
                    "stock_code": stock_code.strip(),
                })

        _corp_code_cache = corps
        return corps
    except Exception:
        return []


@tool
async def naver_search(query: str) -> str:
    """네이버에서 최신 뉴스를 검색합니다. 주식, 시장 동향 관련 최신 소식을 찾을 때 유용합니다.

    Args:
        query: 검색할 키워드 (예: "삼성전자 실적", "반도체 시장 전망")
    """
    if not settings.NAVER_CLIENT_ID or not settings.NAVER_CLIENT_SECRET:
        return "네이버 검색을 사용하려면 .env에 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 설정해주세요."

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": query, "display": 5, "sort": "date"},
                headers={
                    "X-Naver-Client-Id": settings.NAVER_CLIENT_ID,
                    "X-Naver-Client-Secret": settings.NAVER_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return f"네이버 검색 실패: HTTP {e.response.status_code} 오류"
    except httpx.RequestError:
        return "네이버 검색 실패: 네트워크 연결 오류"

    items = data.get("items", [])
    if not items:
        return f"'{query}'에 대한 검색 결과가 없습니다."

    import re
    results = []
    for item in items:
        # HTML 태그 제거
        title = re.sub(r"<[^>]+>", "", item.get("title", ""))
        desc = re.sub(r"<[^>]+>", "", item.get("description", ""))
        pub_date = item.get("pubDate", "")
        link = item.get("link", "")
        results.append(f"- [{title}]({link})\n  {desc}\n  ({pub_date})")

    return f"'{query}' 네이버 뉴스 검색 결과 ({len(items)}건):\n\n" + "\n\n".join(results)


# 해외 주식 한글명 → 티커 매핑 (공통 사용)
_GLOBAL_NAME_TO_TICKER = {
    "애플": "AAPL", "테슬라": "TSLA", "엔비디아": "NVDA",
    "마이크로소프트": "MSFT", "구글": "GOOGL", "알파벳": "GOOGL",
    "아마존": "AMZN", "메타": "META", "페이스북": "META",
    "넷플릭스": "NFLX", "디즈니": "DIS", "나이키": "NKE",
    "코카콜라": "KO", "맥도날드": "MCD", "스타벅스": "SBUX",
    "버크셔해서웨이": "BRK-B", "JP모건": "JPM", "골드만삭스": "GS",
    "인텔": "INTC", "AMD": "AMD", "퀄컴": "QCOM",
    "비자": "V", "마스터카드": "MA", "페이팔": "PYPL",
    "화이자": "PFE", "존슨앤존슨": "JNJ", "모더나": "MRNA",
    "보잉": "BA", "에어버스": "EADSY",
    "토요타": "7203.T", "소니": "6758.T", "닌텐도": "7974.T",
    "텐센트": "0700.HK", "알리바바": "BABA", "바이두": "BIDU",
    "TSMC": "TSM", "삼성SDI": "006400.KS",
}

# 통화 심볼 매핑 (공통 사용)
_CURRENCY_SYMBOL_MAP = {"USD": "$", "JPY": "¥", "HKD": "HK$", "EUR": "€", "GBP": "£"}


async def _resolve_ticker(query: str) -> tuple[str, str, str] | None:
    """한글명/티커 문자열을 (ticker_symbol, display_name, currency_symbol) 튜플로 변환합니다.

    해외 한글명 매핑 → DART 한국 주식 → 직접 티커 순으로 시도하며,
    해석 불가능하면 None을 반환합니다.
    """
    import yfinance as yf

    # 1) 해외 주식 한글명 매핑 확인
    if query in _GLOBAL_NAME_TO_TICKER:
        ticker_symbol = _GLOBAL_NAME_TO_TICKER[query]
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info
            if info and "currentPrice" in info:
                currency = info.get("currency", "USD")
                currency_symbol = _CURRENCY_SYMBOL_MAP.get(currency, currency)
                return ticker_symbol, info.get("shortName", query), currency_symbol
        except Exception:
            return None

    # 2) 한국 주식 — DART 기업코드에서 종목코드 검색
    corps = await _load_corp_codes()
    stock_code = None
    corp_name_found = None
    for c in corps:
        if c["stock_code"] and (c["corp_name"] == query or query in c["corp_name"]):
            stock_code = c["stock_code"]
            corp_name_found = c["corp_name"]
            break

    if stock_code:
        try:
            for suffix in (".KS", ".KQ"):
                ticker_symbol = f"{stock_code}{suffix}"
                ticker = yf.Ticker(ticker_symbol)
                info = ticker.info
                if info and "currentPrice" in info:
                    return ticker_symbol, corp_name_found, "원"
        except Exception:
            return None
        return None

    # 3) 티커 심볼로 직접 조회 (예: "AAPL", "005930.KS", "7203.T")
    ticker_symbol = query.upper()
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        if not info or "currentPrice" not in info:
            return None
        currency = info.get("currency", "USD")
        currency_symbol = _CURRENCY_SYMBOL_MAP.get(currency, currency)
        return ticker_symbol, info.get("shortName", query), currency_symbol
    except Exception:
        return None


@tool
async def get_stock_price(query: str) -> str:
    """한국 및 해외 주식의 현재가, 등락률, 거래량 등 실시간 시세를 조회합니다.

    Args:
        query: 회사명 또는 티커 심볼 (예: "삼성전자", "카카오", "AAPL", "테슬라", "NVDA", "7203.T")
    """
    import yfinance as yf

    resolved = await _resolve_ticker(query)
    if not resolved:
        return f"'{query}'의 시세 정보를 가져올 수 없습니다. 정확한 회사명 또는 티커 심볼을 확인해주세요."

    ticker_symbol, display_name, currency_symbol = resolved
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        if not info or "currentPrice" not in info:
            return f"'{query}'의 시세 정보를 가져올 수 없습니다."
        return _format_stock_info(info, display_name, ticker_symbol, currency_symbol)
    except Exception as e:
        return f"주가 조회 중 오류 발생: {type(e).__name__}"


def _format_stock_info(info: dict, name: str, code: str, currency: str) -> str:
    """주식 시세 정보를 포맷팅합니다."""
    current_price = info.get("currentPrice", 0)
    previous_close = info.get("previousClose", 0)
    change = current_price - previous_close if previous_close else 0
    change_pct = (change / previous_close * 100) if previous_close else 0
    sign = "+" if change >= 0 else ""

    # 통화에 따라 소수점 처리
    is_won = currency == "원"
    fmt = ",.0f" if is_won else ",.2f"

    lines = [
        f"종목: {name} ({code})",
        f"현재가: {current_price:{fmt}}{currency}",
        f"전일대비: {sign}{change:{fmt}}{currency} ({sign}{change_pct:.2f}%)",
        f"전일종가: {previous_close:{fmt}}{currency}",
    ]

    for label, key in [("시가", "open"), ("고가", "dayHigh"), ("저가", "dayLow")]:
        val = info.get(key)
        if isinstance(val, (int, float)):
            lines.append(f"{label}: {val:{fmt}}{currency}")

    vol = info.get("volume")
    if isinstance(vol, (int, float)):
        lines.append(f"거래량: {vol:,}주")

    market_cap = info.get("marketCap")
    if market_cap:
        if is_won:
            lines.append(f"시가총액: {market_cap / 1_0000_0000:,.0f}억원")
        else:
            lines.append(f"시가총액: {currency}{market_cap / 1_000_000_000:,.2f}B")

    for label, key in [("52주 최고", "fiftyTwoWeekHigh"), ("52주 최저", "fiftyTwoWeekLow")]:
        val = info.get(key)
        if isinstance(val, (int, float)):
            lines.append(f"{label}: {val:{fmt}}{currency}")

    per = info.get("trailingPE")
    if isinstance(per, (int, float)):
        lines.append(f"PER: {per:.2f}")

    eps = info.get("trailingEps")
    if isinstance(eps, (int, float)):
        lines.append(f"EPS: {eps:{fmt}}{currency}")

    return "\n".join(lines)


@tool
async def get_technical_indicators(query: str) -> str:
    """한국 및 해외 주식의 기술적 지표(이동평균선, RSI, MACD, 볼린저밴드)를 계산합니다.

    Args:
        query: 회사명 또는 티커 심볼 (예: "삼성전자", "AAPL", "테슬라")
    """
    import yfinance as yf

    resolved = await _resolve_ticker(query)
    if not resolved:
        return f"'{query}'의 종목 정보를 찾을 수 없습니다. 정확한 회사명 또는 티커 심볼을 확인해주세요."

    ticker_symbol, display_name, currency_symbol = resolved

    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="3mo")
        if hist.empty or len(hist) < 20:
            return f"'{display_name}'의 최근 3개월 일봉 데이터가 부족하여 기술적 지표를 계산할 수 없습니다."

        close = hist["Close"]
        current_price = close.iloc[-1]
        is_won = currency_symbol == "원"
        fmt = ",.0f" if is_won else ",.2f"

        # 이동평균선
        ma20 = close.rolling(window=20).mean().iloc[-1]
        ma60 = close.rolling(window=60).mean().iloc[-1] if len(close) >= 60 else None
        ma120 = close.rolling(window=120).mean().iloc[-1] if len(close) >= 120 else None

        # RSI (14일)
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else 0
        rsi = 100 - (100 / (1 + rs))

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_val = macd_line.iloc[-1]
        signal_val = signal_line.iloc[-1]
        macd_hist = macd_val - signal_val

        # 볼린저밴드
        bb_ma = close.rolling(window=20).mean().iloc[-1]
        bb_std = close.rolling(window=20).std().iloc[-1]
        bb_upper = bb_ma + 2 * bb_std
        bb_lower = bb_ma - 2 * bb_std

        # 결과 포맷팅
        lines = [
            f"📊 {display_name} ({ticker_symbol}) 기술적 지표 분석",
            f"현재가: {current_price:{fmt}}{currency_symbol}",
            "",
            "▶ 이동평균선",
            f"  20일선: {ma20:{fmt}}{currency_symbol}",
        ]
        if ma60 is not None:
            lines.append(f"  60일선: {ma60:{fmt}}{currency_symbol}")
        if ma120 is not None:
            lines.append(f"  120일선: {ma120:{fmt}}{currency_symbol}")

        lines += [
            "",
            f"▶ RSI (14일): {rsi:.1f}",
        ]
        if rsi >= 70:
            lines.append(f"  → RSI {rsi:.0f}로 과매수 구간 진입. 단기 조정 가능성에 유의하세요.")
        elif rsi <= 30:
            lines.append(f"  → RSI {rsi:.0f}로 과매도 구간. 반등 가능성을 살펴볼 시점입니다.")
        else:
            lines.append(f"  → RSI {rsi:.0f}로 중립 구간입니다.")

        lines += [
            "",
            "▶ MACD",
            f"  MACD: {macd_val:{fmt}}",
            f"  시그널: {signal_val:{fmt}}",
            f"  히스토그램: {macd_hist:{fmt}}",
        ]
        if macd_val > signal_val:
            lines.append("  → MACD가 시그널 위에 위치하여 매수 신호입니다.")
        else:
            lines.append("  → MACD가 시그널 아래에 위치하여 매도 신호입니다.")

        lines += [
            "",
            "▶ 볼린저밴드 (20일, 2σ)",
            f"  상단: {bb_upper:{fmt}}{currency_symbol}",
            f"  중심: {bb_ma:{fmt}}{currency_symbol}",
            f"  하단: {bb_lower:{fmt}}{currency_symbol}",
        ]
        if current_price > bb_upper:
            lines.append("  → 현재가가 상단밴드 위에 있어 과열 구간입니다.")
        elif current_price < bb_lower:
            lines.append("  → 현재가가 하단밴드 아래에 있어 침체 구간입니다.")
        else:
            lines.append("  → 현재가가 밴드 내에서 정상 범위에 있습니다.")

        # 종합 추세 판단
        lines.append("")
        trend_signals = []
        if current_price > ma20:
            trend_signals.append("20일선 위에서 상승 추세")
        else:
            trend_signals.append("20일선 아래에서 하락 추세")
        lines.append(f"▶ 종합: {', '.join(trend_signals)}")
        lines.append("")
        lines.append("[yfinance]")

        return "\n".join(lines)
    except Exception as e:
        return f"기술적 지표 계산 중 오류 발생: {type(e).__name__}"


@tool
async def get_financial_summary(query: str) -> str:
    """한국 및 해외 주식의 재무제표(손익계산서, 대차대조표, 현금흐름표)를 요약합니다.

    Args:
        query: 회사명 또는 티커 심볼 (예: "삼성전자", "AAPL", "테슬라")
    """
    import yfinance as yf

    resolved = await _resolve_ticker(query)
    if not resolved:
        return f"'{query}'의 종목 정보를 찾을 수 없습니다. 정확한 회사명 또는 티커 심볼을 확인해주세요."

    ticker_symbol, display_name, currency_symbol = resolved

    try:
        ticker = yf.Ticker(ticker_symbol)
        financials = ticker.financials
        balance = ticker.balance_sheet
        cashflow = ticker.cashflow

        if financials is None or financials.empty:
            return f"'{display_name}'의 재무제표 데이터를 가져올 수 없습니다."

        is_won = currency_symbol == "원"
        # 원화는 억원, 달러 등은 B(십억) 단위
        if is_won:
            divisor = 1_0000_0000
            unit = "억원"
        else:
            divisor = 1_000_000_000
            unit = f"{currency_symbol}B"

        def fmt_val(val) -> str:
            """값을 통화 단위로 포맷팅합니다."""
            if val is None or (hasattr(val, '__class__') and val.__class__.__name__ == 'NaTType'):
                return "N/A"
            try:
                converted = float(val) / divisor
                return f"{converted:,.1f}{unit}"
            except (ValueError, TypeError):
                return "N/A"

        # 최근 3개년 (columns가 날짜)
        years = financials.columns[:3]

        lines = [
            f"📋 {display_name} ({ticker_symbol}) 재무제표 요약",
            "",
            "▶ 손익계산서",
        ]

        for year in years:
            year_label = str(year.year) if hasattr(year, "year") else str(year)[:4]
            revenue = financials.loc["Total Revenue", year] if "Total Revenue" in financials.index else None
            op_income = financials.loc["Operating Income", year] if "Operating Income" in financials.index else None
            net_income = financials.loc["Net Income", year] if "Net Income" in financials.index else None
            lines.append(f"  [{year_label}] 매출: {fmt_val(revenue)} | 영업이익: {fmt_val(op_income)} | 순이익: {fmt_val(net_income)}")

        if balance is not None and not balance.empty:
            lines += ["", "▶ 대차대조표"]
            bal_years = balance.columns[:3]
            for year in bal_years:
                year_label = str(year.year) if hasattr(year, "year") else str(year)[:4]
                total_assets = balance.loc["Total Assets", year] if "Total Assets" in balance.index else None
                total_liab = balance.loc["Total Liabilities Net Minority Interest", year] if "Total Liabilities Net Minority Interest" in balance.index else None
                equity = balance.loc["Stockholders Equity", year] if "Stockholders Equity" in balance.index else None
                lines.append(f"  [{year_label}] 총자산: {fmt_val(total_assets)} | 총부채: {fmt_val(total_liab)} | 자본: {fmt_val(equity)}")

                # ROE, 부채비율 계산 (가장 최근 연도만)
                if year == bal_years[0]:
                    try:
                        ni = financials.loc["Net Income", financials.columns[0]] if "Net Income" in financials.index else None
                        eq_val = float(equity) if equity is not None else None
                        tl_val = float(total_liab) if total_liab is not None else None
                        ni_val = float(ni) if ni is not None else None

                        ratios = []
                        if ni_val and eq_val and eq_val != 0:
                            roe = ni_val / eq_val * 100
                            ratios.append(f"ROE: {roe:.1f}%")
                        if tl_val and eq_val and eq_val != 0:
                            debt_ratio = tl_val / eq_val * 100
                            ratios.append(f"부채비율: {debt_ratio:.1f}%")
                        if ratios:
                            lines.append(f"  → 최신 기준 {' | '.join(ratios)}")
                    except (ValueError, TypeError):
                        pass

        if cashflow is not None and not cashflow.empty:
            lines += ["", "▶ 현금흐름표"]
            cf_years = cashflow.columns[:3]
            for year in cf_years:
                year_label = str(year.year) if hasattr(year, "year") else str(year)[:4]
                op_cf = cashflow.loc["Operating Cash Flow", year] if "Operating Cash Flow" in cashflow.index else None
                lines.append(f"  [{year_label}] 영업현금흐름: {fmt_val(op_cf)}")

        lines += ["", "[yfinance]"]
        return "\n".join(lines)
    except Exception as e:
        return f"재무제표 조회 중 오류 발생: {type(e).__name__}"


@tool
async def compare_stocks(query: str) -> str:
    """2~3개 종목을 비교 분석합니다. 현재가, 시가총액, PER, PBR, 배당률, 52주 수익률, 거래량을 비교합니다.

    Args:
        query: 콤마로 구분된 종목명 또는 티커 (예: "삼성전자, SK하이닉스", "AAPL, MSFT, GOOGL")
    """
    import yfinance as yf

    # 종목 파싱
    names = [name.strip() for name in query.split(",") if name.strip()]
    if len(names) < 2:
        return "비교하려면 최소 2개 종목을 콤마(,)로 구분하여 입력해주세요. (예: '삼성전자, SK하이닉스')"
    if len(names) > 3:
        return "비교는 최대 3개 종목까지 가능합니다."

    # 각 종목 정보 수집
    stocks_data = []
    for name in names:
        resolved = await _resolve_ticker(name)
        if not resolved:
            stocks_data.append({"name": name, "error": f"'{name}' 종목을 찾을 수 없습니다."})
            continue

        ticker_symbol, display_name, currency_symbol = resolved
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info
            if not info or "currentPrice" not in info:
                stocks_data.append({"name": name, "error": f"'{name}' 시세 정보 없음"})
                continue

            is_won = currency_symbol == "원"
            current_price = info.get("currentPrice", 0)
            fmt = ",.0f" if is_won else ",.2f"

            # 시가총액
            market_cap = info.get("marketCap")
            if market_cap:
                if is_won:
                    market_cap_str = f"{market_cap / 1_0000_0000:,.0f}억원"
                else:
                    market_cap_str = f"{currency_symbol}{market_cap / 1_000_000_000:,.1f}B"
            else:
                market_cap_str = "N/A"

            # 52주 수익률
            week52_high = info.get("fiftyTwoWeekHigh")
            week52_low = info.get("fiftyTwoWeekLow")
            if week52_low and week52_low > 0:
                week52_return = (current_price - week52_low) / week52_low * 100
                week52_str = f"{week52_return:+.1f}%"
            else:
                week52_str = "N/A"

            per = info.get("trailingPE")
            pbr = info.get("priceToBook")
            div_yield = info.get("dividendYield")
            volume = info.get("volume")

            stocks_data.append({
                "name": display_name,
                "ticker": ticker_symbol,
                "currency": currency_symbol,
                "current_price": f"{current_price:{fmt}}{currency_symbol}",
                "market_cap": market_cap_str,
                "per": f"{per:.2f}" if isinstance(per, (int, float)) else "N/A",
                "pbr": f"{pbr:.2f}" if isinstance(pbr, (int, float)) else "N/A",
                "dividend": f"{div_yield * 100:.2f}%" if isinstance(div_yield, (int, float)) else "N/A",
                "week52_return": week52_str,
                "volume": f"{volume:,}" if isinstance(volume, (int, float)) else "N/A",
            })
        except Exception as e:
            stocks_data.append({"name": name, "error": f"조회 실패: {type(e).__name__}"})

    # 결과 포맷팅 (테이블 형태)
    valid = [s for s in stocks_data if "error" not in s]
    errors = [s for s in stocks_data if "error" in s]

    if not valid:
        return "비교할 수 있는 종목이 없습니다.\n" + "\n".join(s["error"] for s in errors)

    lines = [f"📊 종목 비교 ({', '.join(s['name'] for s in valid)})", ""]

    # 항목별 비교
    headers = ["항목"] + [f"{s['name']}" for s in valid]
    rows = [
        ["현재가"] + [s["current_price"] for s in valid],
        ["시가총액"] + [s["market_cap"] for s in valid],
        ["PER"] + [s["per"] for s in valid],
        ["PBR"] + [s["pbr"] for s in valid],
        ["배당률"] + [s["dividend"] for s in valid],
        ["52주 수익률"] + [s["week52_return"] for s in valid],
        ["거래량"] + [s["volume"] for s in valid],
    ]

    # 각 열의 최대 너비 계산
    all_rows = [headers] + rows
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = max(len(row[col_idx]) for row in all_rows)
        col_widths.append(max(max_width, 8))

    def format_row(row: list[str]) -> str:
        """행을 테이블 형식으로 포맷팅합니다."""
        cells = []
        for i, cell in enumerate(row):
            width = col_widths[i]
            # 한글 문자는 2칸 차지하므로 보정
            korean_chars = sum(1 for c in cell if '\uac00' <= c <= '\ud7a3' or '\u3131' <= c <= '\u318e')
            adjusted_width = width - korean_chars
            cells.append(f"{cell:<{adjusted_width}}")
        return " | ".join(cells)

    lines.append(format_row(headers))
    lines.append("-+-".join("-" * w for w in col_widths))
    for row in rows:
        lines.append(format_row(row))

    if errors:
        lines.append("")
        for s in errors:
            lines.append(f"⚠ {s['error']}")

    lines.append("")
    lines.append("[yfinance]")
    return "\n".join(lines)


@tool
async def search_research_reports(query: str) -> str:
    """증권사 리서치 리포트에서 종목 분석, 산업 전망, 목표주가, 투자의견 등을 검색합니다.
    Elasticsearch에 적재된 네이버 증권 리서치 리포트(종목분석, 산업분석, 경제분석 등)를 하이브리드 검색합니다.

    Args:
        query: 검색 키워드 (예: "삼성전자 실적 전망", "반도체 산업 분석", "2차전지 투자의견")
    """
    es = _get_es_client()
    if es is None:
        return "리서치 리포트 검색을 사용하려면 .env에 ES_URL, ES_USER, ES_PASSWORD를 설정해주세요."

    try:
        # 쿼리 임베딩 생성
        embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=settings.OPENAI_API_KEY,
        )
        query_vector = await embeddings.aembed_query(query)

        # Hybrid 검색 (BM25 + Vector)
        resp = es.search(
            index=RESEARCH_INDEX,
            body={
                "query": {"match": {"content": query}},
                "knn": {
                    "field": "content_vector",
                    "query_vector": query_vector,
                    "k": 5,
                    "num_candidates": 50,
                },
                "size": 5,
            },
        )
    except Exception as e:
        return f"리서치 리포트 검색 실패: {type(e).__name__}: {e}"

    hits = resp["hits"]["hits"]
    if not hits:
        return f"'{query}'에 대한 리서치 리포트 검색 결과가 없습니다."

    results = []
    for i, hit in enumerate(hits, 1):
        source = hit["_source"]
        meta = source.get("metadata", {})
        content = source["content"]

        header = f"[{i}] {meta.get('title', '제목 없음')}"
        if meta.get("stock_name"):
            header += f" - {meta['stock_name']}"
        header += f" ({meta.get('broker', '증권사 미상')}, {meta.get('date', '')})"

        results.append(f"{header}\n{content}")

    return f"'{query}' 리서치 리포트 검색 결과 ({len(hits)}건):\n\n" + "\n\n---\n\n".join(results)
