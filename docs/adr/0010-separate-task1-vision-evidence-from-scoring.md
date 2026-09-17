# Separate Task 1 vision evidence from scoring

C1 sends the original Task 1 pixels to two independent vision routes, reconciles
their ChartFacts in code, validates exact-locator Student Chart Claims against that
result, and only then allows text-only criterion scoring. This deliberate multi-stage
boundary costs extra calls, but prevents one image-to-score response from silently
mixing chart perception, factual accuracy, language quality, and overall authority;
critical visual disagreement disables Task 1 publication without affecting Task 2.
