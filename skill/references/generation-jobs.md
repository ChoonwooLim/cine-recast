# 생성 작업 실행

유료 단계에서 에이전트가 Higgsfield를 부르는 방법과, 그 앞뒤로 도구에 기록하는 순서다. 견적과 생성을 시작하기 전에 끝까지 읽는다. 예시는 SKILL.md와 같이 두 변수를 쓴다.

```powershell
$cr = "$HOME/.cine-recast/bin/cine-recast.ps1"
$p = "$env:USERPROFILE\Videos\cine-recast\my-project"
```

지켜야 할 것은 세 가지다.

- 가격을 묻는 호출은 아무것도 제출하지 않는 호출이어야 한다.
- 생성 호출은 `jobs next`나 `assets next`가 내보낸 작업에 대해서만, 내보낸 내용 그대로, 작업 하나에 한 번 한다.
- 호출의 결과는 성공이든 실패든 도구에 기록한다. 실제로 차감된 크레딧을 적는다.

## Higgsfield 도구

Higgsfield는 MCP 도구로 연결돼 있다. 도구 이름의 앞부분(서버 이름)은 환경마다 다르므로 이름의 끝으로 찾는다.

| 이름의 끝 | 하는 일 |
| --- | --- |
| `models_explore` | 모델 목록과 모델 하나의 정보(파라미터, 길이, 화면비, 입력 역할) |
| `media_upload`, `media_confirm` | 입력 파일 올리기 |
| `generate_video`, `generate_image` | 생성 작업 제출. 크레딧이 나간다 |
| `jobs_wait` | 제출한 작업이 끝나기를 기다린다 |
| `balance`, `transactions` | 남은 크레딧, 실제 차감 내역 |

환경에 따라 도구가 불러오기 전까지 이름만 보이고 설명과 파라미터가 없을 수 있다. 그때는 환경의 방법으로 먼저 불러온다(Claude Code에서는 ToolSearch). 이름이 목록에 있으면 연결은 있는 것이다. 자신의 도구 목록에 이 이름으로 끝나는 도구가 없으면 Higgsfield 연결이 없는 것이다. 견적과 생성을 하지 않고 사용자에게 알린다. 서비스는 자주 바뀐다. 도구 자체의 설명이 이 문서와 다르면 호출 방법은 도구의 설명을 따른다. 승인과 장부에 관한 규칙은 그래도 바뀌지 않는다.

## models.json 채우기

모델의 한도는 코드에도 이 문서에도 고정돼 있지 않다. 계획을 세우는 시점에 `models_explore`에서 읽어 프로젝트의 `models.json`에 적는다.

1. 후보를 찾는다. `models_explore`에 `action: "list"`와 `type: "video"`(자료 생성용은 `"image"`)를 준다.
2. 쓸 모델마다 `action: "get"`과 `model_id`를 주어 파라미터, 길이, 화면비, 입력 역할을 읽는다.
3. 읽은 값을 아래 키로 옮겨 적는다.

| 키 | 적는 값 |
| --- | --- |
| `id` | 모델 id 그대로. 생성 호출의 `model`로 나간다 |
| `kind` | 영상 모델은 `video`, 이미지 모델은 `image` |
| `min_seconds`, `max_seconds` | 영상 모델이 받는 길이의 최소와 최대(초). `max_seconds`는 생성 단위 길이의 상한이 된다. `edit`로 쓸 모델에 참조 영상 길이의 한도가 따로 있으면 둘 중 작은 값을 적는다. `models_explore`가 참조 영상 길이의 한도를 따로 말하지 않으면 `max_seconds`를 그대로 쓰고, 그렇게 가정했다고 `notes`에 적고 사용자에게 알린다 |
| `aspect_ratios` | 모델의 화면비 가운데 `W:H` 꼴인 것 |
| `resolutions` | 모델의 해상도를 낮은 것부터. 초안 작업은 첫 값, 본 생성 작업은 마지막 값으로 나간다. 이미지 모델에 해상도 항목이 없으면 `[]` |
| `video_ref` | 영상 모델이 참조 영상을 입력으로 받으면 `true`. `edit` 방법에 필요하다 |
| `image_refs` | 한 호출에 받는 이미지 입력의 최대 수 |
| `read_at` | 읽은 날짜 |

```json
{
  "models": [
    {
      "id": "seedance_2_5",
      "kind": "video",
      "min_seconds": 4,
      "max_seconds": 15,
      "aspect_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
      "resolutions": ["480p", "720p", "1080p"],
      "video_ref": true,
      "image_refs": 4,
      "read_at": "2026-11-18",
      "notes": {
        "edit": {"mode": "video_edit", "video": "video_references", "image": "image_references"},
        "regen": {"mode": "omni_reference", "image": "image_references"}
      }
    },
    {
      "id": "image_model_x",
      "kind": "image",
      "aspect_ratios": ["1:1", "3:4", "16:9", "21:9"],
      "resolutions": [],
      "image_refs": 1,
      "read_at": "2026-11-18",
      "notes": {"quality": "2k", "image": "image"}
    }
  ]
}
```

id와 값은 형식을 보여 주는 예시다. 실제 값은 `models_explore`에서 읽는다.

```powershell
& $cr models check $p
```

- **본 생성의 해상도**는 `resolutions`의 마지막 값이다. 사용자와 정한 마감 해상도보다 높은 값은 목록에서 뺀다. 견적을 받기 전에 정한다.
- **모델에만 있는 항목**(`mode` 같은 것)과 입력 역할의 이름은 도구가 다루지 않는다. 위의 `notes`처럼 그 모델 항목에 메모로 적어 둔다. 도구는 모르는 키를 무시한다. 가격을 물을 때와 생성할 때, 그리고 다음 세션에서도 같은 값을 쓰기 위해서다.
- **소리와 초안 스위치**: 모델에 소리를 만드는 옵션(이름이 `generate_audio` 같은 것)이 있으면 끈다. 완성본에는 생성된 소리를 쓰지 않고, `conform`은 화면만 남긴다. `draft`처럼 이름이 비슷한 공급자 쪽 스위치는 도구의 `--quality`와 별개이므로 기본값으로 둔다. 무엇을 골랐든 그 모델의 `notes`에 적고, 가격을 물을 때와 생성할 때 똑같이 넣는다.
- **모델이 정해진 길이만 받으면**(예: 5초와 10초) `plan build` 뒤에 `plan.json`의 `units[].params.duration`을 단위 길이보다 짧지 않은 허용 값으로 고친다.
- 견적을 받기 전에 `jobs list`의 `duration`, `aspect_ratio`, `resolution`이 `models_explore`가 알려 준 허용 값인지 확인한다. 서비스는 허용되지 않는 값을 가까운 값으로 바꿔 실행할 수 있고, 그러면 기록과 실제가 달라진다.

## 입력 파일 올리기

생성 호출은 media id만 받는다. URL이나 로컬 경로는 넣을 수 없다. 그래서 입력 파일을 먼저 올린다.

올리기 전에 검사를 통과시킨다. 영상 작업의 입력은 `cast check`와 `plan check`가 통과한 뒤에 올리고, 자료 작업의 참조 이미지는 `assets add`가 받아 준 뒤에 올린다(그 검사가 권리 답을 읽는다). 그리고 어떤 파일을 서비스로 보내려는지 사용자에게 한 줄로 알린다. 올리는 데 크레딧은 들지 않지만 파일이 이 컴퓨터를 떠난다.

1. `media_upload`에 파일 이름을 준다. 하나면 `filename`, 여럿이면 `files[]`. 파일마다 업로드 URL과 media id가 돌아온다.
2. 업로드 URL에 파일의 바이트를 PUT으로 보낸다. 응답에 curl 명령이 들어 있으면 그 명령을 쓴다.
3. PUT이 모두 성공한 뒤 `media_confirm`에 media id와 `type`(`image` 또는 `video`)을 준다.

같은 파일의 media id는 가격을 물을 때와 생성할 때 다시 쓴다. id를 잃었으면 다시 올린다.

도구가 알려 주는 입력의 역할은 `video`와 `image` 둘뿐이고, 모델의 역할 이름은 모델마다 다르다. `models_explore`에서 그 모델의 역할을 읽어 바꿔 넣는다. `edit` 작업의 `video` 입력은 편집할 원본 영상이고, `image` 입력은 교체 이미지다. `regen` 작업의 첫 `image` 입력은 원본 숏의 첫 스틸이다. 이것은 구도를 참조하는 역할로 넣고, 출력의 첫 프레임이 되는 역할로는 넣지 않는다. 원본 화면이 결과에 그대로 들어가기 때문이다.

## 가격 묻기

가격은 제출 없이 비용만 돌려주는 호출로 묻는다. 환경에 따라 방법이 다르므로 자신의 도구 설명을 읽고 고른다.

- 견적 전용 도구가 있으면 그것을 쓴다. 이름이 `estimate_video_cost`, `estimate_image_cost`로 끝난다. 생성 호출에 넣을 것과 같은 `params`를 주면 크레딧을 돌려주고 아무것도 제출하지 않는다.
- 견적 전용 도구가 없고, `generate_video`와 `generate_image`의 도구 설명이 `get_cost: true`를 주면 제출 없이 비용만 돌려준다고 밝히고 있을 때에만 `params`에 `get_cost: true`를 넣어 그 도구를 부른다.
- 둘 다 아니면 가격을 물을 방법이 없는 것이다. 생성 도구를 시험 삼아 부르지 않는다. 사용자에게 알리고 멈춘다.

`get_cost`를 받지 않는 생성 도구에 `get_cost: true`를 넣으면 그 항목이 무시되고 실제 생성이 제출될 수 있다. 승인 전의 유료 호출이 되므로, 도구 설명으로 확인한 뒤에만 쓴다.

견적 호출도 실제로 올리고 `media_confirm`까지 마친 파일의 media id만 받는다. 올리지 않았거나 지어낸 id는 형식 오류나 "찾을 수 없음"으로 거부된다. 그래서 입력 파일은 가격을 묻기 전에 올린다("입력 파일 올리기").

가격을 물을 때 넣는 값은 나중에 실제로 보낼 값과 같아야 한다.

- `jobs list`(자료는 `assets list`)에 나온 그 작업의 `params` 전부.
- 입력 파일의 media id와 역할. 입력의 길이나 수가 가격을 바꾸는 모델이 있으므로 입력도 넣어서 묻는다. 영상 작업의 입력은 `plan show`의 `units[].inputs`에, 자료 작업의 입력은 `assets add`에 준 `--ref`에 있다.
- 모델에만 있는 항목(`models.json`의 메모).

돌려받은 크레딧을 기록한다.

```powershell
& $cr jobs list $p
& $cr plan show $p
& $cr jobs set-cost $p --job j0001 --credits 40
```

## 영상 작업 하나의 순서

1. **입력 파일 올리기.** `cast check`와 `plan check`가 통과했는지 보고, 보낼 파일을 사용자에게 한 줄로 알린 뒤, 단위의 입력(`plan show`의 `units[].inputs`)을 올리고 media id를 받는다.
2. **비용 조회.** "가격 묻기"대로 한다. 견적 전용 도구를 쓰거나, 환경이 지원할 때에만 `get_cost: true`를 쓴다.
3. **`jobs set-cost`.** 받은 크레딧을 작업에 기록한다.
4. **사용자 승인.** 작업별 견적과 합계를 표로 보여 주고, 사용자가 말한 한도를 받는다(SKILL.md의 "비용 승인").
5. **`approve`.** 그 한도를 장부에 기록한다.

   ```powershell
   & $cr approve $p --batch draft-1 --limit 80 --by owner --note "drafts for u01, one retry included"
   ```
6. **`jobs next`.** 장부가 받아 준 작업이 JSON으로 나온다. 이 출력이 실행 지시의 전부다.

   ```powershell
   & $cr jobs next $p --batch draft-1
   ```

   ```json
   [
     {
       "job_id": "j0001",
       "unit": "u01",
       "tool": "generate_video",
       "params": {
         "model": "seedance_2_5",
         "prompt": "Replace the teenage boy riding the dragon with the woman shown in the first reference image ...",
         "duration": 4,
         "aspect_ratio": "16:9",
         "resolution": "480p"
       },
       "medias": [
         {"role": "video", "path": "C:\\Users\\me\\Videos\\cine-recast\\my-project\\analysis\\units\\u01\\ref.mp4"},
         {"role": "image", "path": "C:\\Users\\me\\Videos\\cine-recast\\my-project\\assets\\hero\\a0001_2.png"}
       ]
     }
   ]
   ```
7. **생성 호출.** 항목마다 `tool`에 적힌 도구를 한 번 부른다. `params`에는 출력의 `model`, `prompt`, `duration`, `aspect_ratio`, `resolution`을 글자 그대로 넣고, `medias`에는 출력의 입력을 같은 순서로 `{role, value}`로 넣는다. `role`은 모델의 역할 이름, `value`는 그 파일의 media id다. 모델에만 있는 항목은 가격을 물을 때와 같은 값으로 넣는다. `count`는 넣지 않는다. 작업 하나는 테이크 하나다. 응답이 돌려준 작업 id를 적어 둔다.
8. **완료 대기.** `jobs_wait`에 `{index, job_id}`를 준다. 한 번에 12개까지 넣을 수 있고 한 번에 15초까지만 기다리므로, 끝났다고 할 때까지 되풀이해 부른다.
9. **결과 파일 내려받기.** 끝난 작업이 돌려준 URL에서 파일을 로컬로 받고, 영상으로 읽히는지 확인한다. URL은 작은따옴표로 감싼다.

   ```powershell
   New-Item -ItemType Directory -Force "$p\downloads" | Out-Null
   curl.exe -sS -L --fail -o "$p\downloads\j0001.mp4" 'https://example.com/result.mp4'
   ffprobe -v error -show_entries format=duration -of csv=p=0 "$p\downloads\j0001.mp4"
   ```
10. **`jobs done`.** `--provider-id`에 Higgsfield의 작업 id, `--credits`에 실제로 차감된 크레딧(`transactions`에서 확인한다), `--file`에 받은 파일을 준다.

    ```powershell
    & $cr jobs done $p --job j0001 --provider-id 7c2f0e1a-5b1d-4c7e-9a43-0d6f2b8e1c55 --credits 38 --file "$p\downloads\j0001.mp4"
    ```

`jobs next`가 작업을 여럿 내보내면 7번을 작업마다 하고, `jobs_wait` 한 번에 함께 기다릴 수 있다. 도구의 작업 id(`j0001`)와 Higgsfield의 작업 id를 짝지어 적어 두어 결과가 섞이지 않게 한다.

생성 호출이 작업 id 대신 "제출하지 않았다"는 답과 사용자에게 물을 질문을 돌려주는 경우가 있다(예: 무료 생성분을 쓸지). 그때는 제출된 것이 없으므로 사용자에게 그대로 묻고, 답을 넣어 다시 부른다. 무료 생성분으로 처리되어 차감이 없었으면 `--credits 0`으로 기록한다.

## 자료 이미지 작업 하나의 순서

영상 작업과 같고, 명령과 도구만 다르다.

1. 참조 이미지(`--ref`)가 있으면, 보낼 파일을 사용자에게 한 줄로 알린 뒤 올린다.
2. 가격을 묻는다. `assets list`의 `params`(`model`, `prompt`, `aspect_ratio`, `count`, 있으면 `resolution`)를 쓴다.

   ```powershell
   & $cr assets list $p
   & $cr assets set-cost $p --asset a0001 --credits 6
   ```
3. 표를 보여 주고 승인받아 기록한 뒤 내보낸다.

   ```powershell
   & $cr approve $p --batch assets-1 --limit 10 --by owner
   & $cr assets next $p --batch assets-1
   ```
4. 출력의 항목마다 `generate_image`를 한 번 부른다. `params`의 값을 그대로 넣고(`count` 포함), `medias`의 참조 이미지를 media id로 넣는다.
5. `jobs_wait`로 기다리고, 돌려받은 이미지를 모두 내려받아 등록한다. `--file`은 이미지마다 한 번씩 쓴다. 호출 하나가 작업 id를 여러 개 돌려주면 쉼표로 이어 따옴표로 감싸 적는다.

   ```powershell
   & $cr assets done $p --asset a0001 --provider-id "2b9d4c1e-7f3a-4e5b-8c6d-1a2b3c4d5e6f,9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b" --credits 5.5 --file "$p\downloads\a0001_1.png" --file "$p\downloads\a0001_2.png"
   ```
6. 후보를 사용자에게 보여 주고, 고른 한 장을 교체 자료로 등록한 뒤 교체표를 검사한다.

   ```powershell
   & $cr assets use $p --element hero --image assets/hero/a0001_2.png
   & $cr cast check $p
   ```

## 결과를 모를 때

생성 호출이 응답 없이 끊기거나 전송 시간 초과가 나면, 제출됐는지 안 됐는지 알 수 없다. **다시 제출하지 않는다.** 접수된 호출이었다면 같은 작업에 크레딧이 두 번 나간다.

1. 먼저 조회한다. `transactions`와 `balance`로 그 시각에 차감이 있었는지 본다. 환경에 최근 생성 작업을 보여 주는 도구가 있으면 그것으로 작업 id를 찾는다.
2. 제출된 것으로 확인되면 그 작업 id로 `jobs_wait`를 불러 기다리고, 평소대로 내려받아 `jobs done`으로 기록한다.
3. 제출되지 않은 것으로 확인되면 같은 작업을 한 번 다시 제출해도 된다.
4. 확인할 수 없으면 사용자에게 상황을 그대로 알리고 어떻게 할지 묻는다.

`jobs_wait`가 시간 안에 끝나지 않는 것은 실패가 아니다. 작업이 아직 도는 중이니 같은 작업 id로 다시 기다린다.

## 실패와 재시도

서비스가 작업의 실패를 알리면(오류, 내용 거부 등) 서비스가 말한 이유와 함께 기록한다. `--credits`에는 실패했는데도 차감된 크레딧을 적는다. 차감이 없었으면 0이다.

```powershell
& $cr jobs fail $p --job j0002 --reason "provider error: content rejected" --credits 0
& $cr assets fail $p --asset a0002 --reason "provider rejected the prompt" --credits 0
```

- 결과 파일은 왔는데 품질이 나쁜 것은 실패가 아니다. 크레딧이 나갔으므로 `jobs done`으로 기록해 테이크로 남기고, 그다음에 재시도한다.
- 내보냈지만 제출하지 않기로 한 작업도 `jobs fail`로 닫는다(이유에 제출하지 않았다고 적고 크레딧은 0). 닫지 않은 작업은 견적만큼 한도를 계속 차지한다.
- 재시도는 새 작업이다. 같은 단위에 작업을 더하고, 필요하면 프롬프트를 고치고, 가격을 기록한 뒤 다시 내보낸다. 승인 한도 안이면 새 승인 없이 나가고, 한도를 넘으면 `jobs next`가 거부하니 사용자에게 다시 승인받는다. 자료는 `assets add`로 새 자료를 만든다.

  ```powershell
  & $cr jobs add $p --unit u01 --batch draft-1 --quality draft
  ```

## 끊긴 작업 이어가기

세션을 이어받았을 때 `jobs list`나 `assets list`에 `state`가 `issued`인 작업이 있으면, 내보냈지만 결과가 기록되지 않은 작업이다. 이전 세션이 제출했는지 알 수 없으므로 "결과를 모를 때"와 똑같이 먼저 조회한다.

- 제출돼 있었으면 끝나기를 기다려 `jobs done`이나 `jobs fail`로 기록한다.
- 제출되지 않았으면 지금 제출한다. `jobs list`에 내보낼 때의 `params`가 그대로 나오고, 입력 파일은 `plan show`의 `units[].inputs`에 있다.
- 확인할 수 없으면 사용자에게 묻는다.

## 장부 점검과 사용 내역 대조

묶음 하나를 마칠 때마다, 그리고 마지막 보고 전에 한다.

```powershell
& $cr audit $p
```

- `ok`가 `true`이고 `problems`가 비어 있어야 한다.
- `batches`의 묶음마다 `limit`(승인 한도), `committed`(쓴 크레딧과, 끝나지 않은 작업의 견적), `remaining`(남은 한도)을 본다. `totals`는 모든 묶음을 더한 같은 세 값이다.
- `provider_ids`는 끝난 작업의 Higgsfield 작업 id다. `report`가 쓰는 보고서의 비용 아래에도 같은 목록이 있다.

Higgsfield의 `transactions`와 대조한다.

1. `provider_ids`의 작업마다 그에 해당하는 차감이 내역에 있는가.
2. 이 프로젝트를 하는 동안의 차감 가운데 장부에 없는 것이 있는가. 있으면 승인 기록 없는 유료 호출이다.
3. 묶음별 사용액의 합(`totals`의 `committed`, 끝나지 않은 작업이 없을 때)이 내역의 차감 합과 맞는가.

어긋나는 것이 있으면 고치려 하지 말고 무엇이 얼마나 다른지 사용자에게 그대로 보고한다.

명령이 `ledger line N is invalid`로 멈추면 장부 파일이 손상된 것이다. 손상된 동안에는 `audit`만 돌아가고, `audit`이 문제 줄을 알려 준다. 생성을 멈추고 사용자에게 보고한다. `ledger.jsonl`은 에이전트가 고치지 않는다. 승인 기록을 에이전트가 고칠 수 있으면 승인의 의미가 없어지기 때문이다.
