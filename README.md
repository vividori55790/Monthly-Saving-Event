# Monthly Saving Event

네이버페이, 삼성월렛, 편의점의 30% 이상 할인·적립 행사를 뉴스와 커뮤니티에서 찾아 이메일로 보냅니다. GitHub Actions에서 무료로 돌아가며 서버가 필요 없습니다.

## 처음 한 번만 할 일

메일을 보내려면 Gmail 앱 비밀번호가 필요합니다.

1. [Google 앱 비밀번호](https://myaccount.google.com/apppasswords)에서 비밀번호를 만듭니다. 2단계 인증이 켜져 있어야 합니다.
2. 이 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서 이름은 `MAIL_APP_PASSWORD`, 값은 방금 만든 비밀번호로 저장합니다.

## 설정 바꾸기

[config.toml](config.toml)만 고치면 됩니다.

| 바꾸고 싶은 것 | 항목 |
|---|---|
| 받는 주소 | `delivery.recipient` |
| 전송 주기 (매일, 매주, 매월, 수동) | `delivery.mode`, `hour`, `weekday`, `day_of_month` |
| 기준 비율 | `rules.threshold` |
| 찾을 대상과 제외할 말 | `keywords` |
| 뉴스 검색어 | `news.queries` |
| 수집 소스 | `community.rss` |

## 지금 바로 보내기

**Actions → 행사 수집 및 메일 전송 → Run workflow**를 누릅니다. 아직 보내지 않은 행사가 바로 전송됩니다. 이미 보낸 행사까지 다시 받으려면 "이미 보낸 진행 중 행사도 포함"을 켭니다. 보낼 행사가 없으면 메일은 오지 않습니다.

## 동작 방식

1. **수집:** 커뮤니티 게시판은 약 15분마다, 뉴스는 6시간마다 읽습니다. 실패한 소스는 재시도 간격을 최대 6시간까지 늘립니다.
2. **판별:** 제목의 키워드로 대상과 혜택을 찾고, 비율이나 "1만원 쓰면 5000원 적립" 같은 금액을 계산합니다. 30% 이상만 남깁니다.
3. **제외:** 통신사, 픽업, 특정 카드, 멤버십, 신규 가입 조건, 1+1·2+1 같은 증정 행사, 끝난 행사를 뺍니다.
4. **정리:** 같은 행사의 기사를 묶고, 메일에는 행사 정보와 출처만 넣습니다.

소스 하나가 24시간 넘게 실패하면 실행이 실패로 표시되어 GitHub가 실패 알림 메일을 보냅니다. 이 알림은 행사 메일과 별개입니다.

## 선택: 무료 LLM 보조

Secrets에 `GEMINI_API_KEY`를 넣으면, 할인율을 못 읽은 글에 한해 Gemini에 물어봅니다. 키가 없거나 API가 끊기면 그 실행에서는 호출을 멈추고 키워드 결과만으로 계속 동작합니다. 공개된 글의 제목과 요약만 보냅니다.

## 한계

- 앱 안에서만 보이는 행사와 본문이 이미지뿐인 공지는 찾지 못합니다.
- 참여 조건이 제목과 요약에 없으면 걸러지지 않으니, 참여 전에 출처를 확인하세요.
- Google 뉴스로만 찾은 기사는 Bing에서 원문을 찾지 못하면 Google을 거치는 링크로 표시됩니다.

## 로컬에서 확인

Python 3.11 이상이면 설치할 것이 없습니다.

```
python -m unittest discover -s tests -t .
python -m saving_events --preview --state preview_state.json
```
