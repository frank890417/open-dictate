# 隱私模型 / Privacy Model

Open Dictate is local-first. Audio, transcripts, logs, review queues, and speaker profiles should stay on the user's Mac unless the user explicitly exports them.

Open Dictate 是本地優先工具。音檔、逐字稿、日誌、審核佇列、說話者資料預設都留在使用者自己的 Mac；除非使用者明確匯出，專案不應把它們送出本機。

## 資料分類 / Data Classes

| 類型 | 預設位置 | 是否可進 repo |
|---|---|---|
| Dictation logs / 語音輸入日誌 | `~/.open-dictate/dictation-log/` | No |
| Meeting transcripts / 會議逐字稿 | `~/.open-dictate/meetings/` | No |
| Review queue / 誤聽候選審核 | `~/.open-dictate/review-queue/` | No |
| Personal glossary / 個人詞庫 | `~/.open-dictate/glossaries/` | No by default |
| Speaker profiles / 說話者資料 | `~/.open-dictate/speakers/` | Never |
| Public fixtures / 公開測試資料 | `fixtures/` or `examples/` | Yes, fictional or public-domain only |

## Speaker Profiles Are Sensitive

Speaker embeddings and voiceprints are biometric data. Treat them like secrets:

- Keep them local.
- Do not commit them.
- Do not paste them into GitHub issues.
- Enroll speakers only with consent.
- Prefer anonymous `SPEAKER_00`, `SPEAKER_01` labels when sharing transcripts.

聲紋與說話者 embedding 屬於生物特徵資料。請把它們當成敏感資料處理：只放本機、不進 Git、不貼到 issue、取得同意才建檔；要分享逐字稿時，優先使用匿名說話者標籤。

## Self-evolving Glossary

Open Dictate may suggest possible mishearings, but suggestions should enter a review queue first. Accepted pairs can update the user's local glossary; rejected pairs are remembered so the same weak guess is not repeated endlessly.

Open Dictate 可以自動提出疑似誤聽，但候選應先進審核佇列。使用者接受後才寫入本機詞庫；拒絕的候選會被記住，避免同一個弱猜測反覆出現。

## Public Contributions

Before opening a pull request, run:

```bash
python3 scripts/public-safety-scan.py
python3 -m unittest discover tests
```

Do not include real audio, real transcripts, real names, private logs, or speaker profiles in PRs.
