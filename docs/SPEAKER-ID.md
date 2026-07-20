# 說話者辨識 / Speaker Identity

Open Dictate separates two concerns:

1. **Anonymous speaker labels**: `SPEAKER_00`, `SPEAKER_01`.
2. **Optional local speaker identity**: user-owned speaker profiles stored outside the repo.

Open Dictate 把兩件事分開：

1. **匿名說話者標籤**：`SPEAKER_00`、`SPEAKER_01`。
2. **可選的本機說話者身分**：由使用者自己建立，存放在 repo 外。

## Default: Anonymous

The public-safe default is anonymous labeling. This is suitable for sharing examples, tests, and public transcripts.

公開安全預設是匿名標籤，適合分享範例、測試與公開逐字稿。

## Optional: Local Profiles

Future local speaker profiles should live under:

```text
~/.open-dictate/speakers/
```

They must not be committed. They may include biometric embeddings, so treat them as sensitive data.

## Confidence Rule

If the system is not confident, it should say `unknown`, not guess a name.

不確定時輸出 unknown，不硬猜人名。
