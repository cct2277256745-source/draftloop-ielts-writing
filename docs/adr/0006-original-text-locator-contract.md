# Keep P2 locators on exact original Unicode code points

P2-01 uses content-addressed Submission Snapshots and zero-based, half-open Python Unicode code-point locators over exact original text, while normalized text is navigation-only. This preserves quoted learner evidence across P2-02 and revision work without the ambiguity of byte, UTF-16, or normalized-text offsets; learner-state observations remain scoreable and are not confused with analyzer uncertainty.
