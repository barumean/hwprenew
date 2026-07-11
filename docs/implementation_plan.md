# 표·그림틀·사진 너비 조절 기능 추가

> [!NOTE]
> **상태: 구현 완료 (2026-07)** — 표/그림틀 너비 조절(`resize_table`)과 사진 너비 조절(`resize_picture`, 비율 유지)이 모두 `표정리.py`에 반영되었습니다.
> - Open Question **Q1**(사진 너비 조절 필요 여부): 기능은 구현하되 기본값 `유지`로 두어, 사용자가 YAML에서 명시적으로 켜야 동작합니다.
> - Open Question **Q2**(표 너비 기본값): 배포하는 `서식규칙.yaml`에는 `문서폭`으로 설정(적극적), 코드상 키 생략 시 기본값은 `유지`(보수적)입니다.
> - 편집 영역 폭 계산 시 제본여백(GutterLen)도 함께 제외하도록 계획에서 한 가지 보완되었습니다.

## 배경

현재 `표정리.py`는 테두리 선·배경색·번호종류 등 **서식**만 정리하고, 표/그림틀/사진의 **크기(너비)**는 건드리지 않습니다.
사용자가 문서 편집 용지 폭에 맞추거나 원하는 값으로 일괄 조절하고 싶어합니다.

## 사용 가능한 API

조사 결과, pyhwpx COM API에서 다음을 지원합니다:

| 기능 | API |
|---|---|
| 편집 용지 폭 읽기 | `hwp.get_pagedef_as_dict()` → `PaperWidth`, `LeftMargin`, `RightMargin` (HwpUnit) |
| 표 너비 변경 | `ctrl.Properties`에서 `Width` 항목 설정 (HwpUnit) |
| 그림 개체 크기 변경 | `ShapeObjDialog` 액션의 `Width`/`Height` 항목 |
| 단위 변환 | `hwp.MiliToHwpUnit(mm)` |

> [!NOTE]
> HwpUnit: 1인치 = 7200 HU, 1mm ≈ 283.465 HU

---

## 제안: `서식규칙.yaml` 확장

```yaml
# 기존 섹션 아래에 추가

표서식:
  # ... 기존 바깥선/안쪽선/머리글행/본문셀 유지 ...
  너비: 문서폭           # 문서폭 = 편집영역(용지폭-좌우여백)에 맞춤
                          # 150mm  = 150mm 고정
                          # 유지   = 현재 너비를 바꾸지 않음 (기본값)

그림틀:
  # ... 기존 처리/테두리/배경/번호종류 유지 ...
  너비: 문서폭           # 문서폭 / 고정값(mm) / 유지

사진:
  # ... 기존 번호종류 유지 ...
  너비: 유지              # 문서폭 / 고정값(mm) / 유지
```

### 값 설명

| 값 | 의미 |
|---|---|
| `유지` (기본) | 너비를 변경하지 않음 — 기존 동작과 동일 |
| `문서폭` | 편집 영역 폭(`용지폭 - 좌여백 - 우여백`)에 자동 맞춤 |
| `150mm` 등 | 해당 mm 값으로 고정 너비 설정 |

> [!IMPORTANT]
> **높이 처리**: 너비를 변경할 때 높이는 **비율 유지**(원래 가로세로 비율을 계산하여 자동 조정)할지, **고정 유지**(높이는 건드리지 않음)할지 결정이 필요합니다.
> - **표**: 높이는 내용에 따라 자동 결정되므로 너비만 변경하면 됨
> - **그림틀(1×1 표)**: 표와 동일 — 너비만 변경
> - **사진(그림 개체)**: 비율 유지가 자연스러움 (찌그러짐 방지)

---

## Proposed Changes

### [MODIFY] [서식규칙.yaml](file:///c:/Users/goning/Documents/gemini/hwp_renew%20(2)/서식규칙.yaml)

각 섹션에 `너비: 유지` 항목을 추가합니다 (기본값 = 기존 동작 유지):

```diff
 표서식:
+  너비: 문서폭           # 문서폭 / 150mm 등 고정값 / 유지
   바깥선:
     ...

 그림틀:
+  너비: 문서폭           # 문서폭 / 150mm 등 고정값 / 유지
   처리: 정리
   ...

 사진:
+  너비: 유지             # 문서폭 / 150mm 등 고정값 / 유지
   번호종류: 없음
```

---

### [MODIFY] [표정리.py](file:///c:/Users/goning/Documents/gemini/hwp_renew%20(2)/표정리.py)

#### 1. 규칙 파싱 (`load_rules` 함수)

- `너비` 값을 파싱하여 `{"mode": "doc_width" | "fixed" | "keep", "value_mm": float | None}` 구조로 저장
- `parse_width_rule(raw_value)` 헬퍼 함수 추가

#### 2. 편집 영역 폭 계산 (`get_edit_width` 함수 신규)

```python
def get_edit_width(hwp) -> int:
    """편집 영역 폭(HwpUnit) = 용지폭 - 좌여백 - 우여백"""
    pd = hwp.get_pagedef_as_dict()
    return pd['PaperWidth'] - pd['LeftMargin'] - pd['RightMargin']
```

#### 3. 표 너비 변경 (`resize_table` 함수 신규)

```python
def resize_table(hwp, ctrl, target_hu: int) -> bool:
    """표 컨트롤의 너비를 target_hu(HwpUnit)로 변경"""
    props = ctrl.Properties
    if props.Item("Width") == target_hu:
        return False
    props.SetItem("Width", target_hu)
    ctrl.Properties = props
    return True
```

#### 4. 사진 너비 변경 (`resize_picture` 함수 신규)

```python
def resize_picture(hwp, ctrl, target_hu: int) -> bool:
    """그림 개체의 너비를 변경 (높이는 비율 유지)"""
    props = ctrl.Properties
    cur_w = props.Item("Width")
    cur_h = props.Item("Height")
    if cur_w == target_hu:
        return False
    new_h = int(cur_h * target_hu / cur_w)  # 비율 유지
    props.SetItem("Width", target_hu)
    props.SetItem("Height", new_h)
    ctrl.Properties = props
    return True
```

#### 5. `process()` 함수에서 호출

- 표/그림틀 정리 루프에서 서식 적용 후 너비 변경 호출
- 개체 속성 정리 루프에서 사진 너비 변경 호출

---

## Open Questions

> [!IMPORTANT]
> **Q1. 사진(그림 개체) 너비 조절이 필요한가요?**
> 사진은 다양한 크기가 의도적일 수 있어서, 기본값을 `유지`로 두었습니다.
> 사진도 문서폭 맞춤이 필요하시면 기본값을 `문서폭`으로 바꿀 수 있습니다.

> [!IMPORTANT]
> **Q2. 표 너비의 기본값을 `문서폭`으로 할까요, `유지`로 할까요?**
> - `문서폭`: 이 기능을 추가하면 바로 모든 표에 적용됨 (적극적)
> - `유지`: 사용자가 YAML에서 명시적으로 켜야 적용됨 (보수적, 기존 동작 유지)

---

## Verification Plan

### Manual Verification
1. `서식규칙.yaml`에 `너비: 문서폭` 설정 후 샘플 문서 실행 → 모든 표가 편집 영역 폭으로 조절되는지 확인
2. `너비: 120mm` 등 고정값 설정 후 실행 → 해당 mm로 적용되는지 확인
3. `너비: 유지` 설정 → 기존과 동일하게 너비 변경 없이 동작하는지 확인
4. 사진 개체에 `문서폭` 적용 시 비율 유지 확인
