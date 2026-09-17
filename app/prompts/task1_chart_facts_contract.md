Use this exact field convention so independently observed facts can be matched
without treating a paraphrase as a different fact. All fields in requiredOutputShape
are mandatory; no extra fields. All value, unit, subject, category and timePoint
fields are strings, including numbers.

- Copy category and time labels literally from the image. Each nonempty fact
  category, unit and timePoint must also occur in the top-level categories, units
  and timePoints arrays respectively. Do not create combined categories such as
  "Bus; Rail" or combined time points such as "2000-2020". For a fact spanning
  categories or years, use an empty category or timePoint, as appropriate.
- VALUE facts: one fact per visible data point. subject is exactly its printed
  series/category label; category repeats that label; timePoint is the single
  printed time label. value is only a finite number string, e.g. "20", with no
  words, units, separators or range notation. unit is the literal axis/unit label.
  direction is NOT_APPLICABLE for an individual data point.
- TREND facts: one per series for the whole displayed period. subject and
  category are the exact series label; timePoint and unit are empty strings.
  A qualitative direction has no numerical unit; keep the axis unit on VALUE
  facts only. value and direction
  are the same one of INCREASE, DECREASE, STABLE or MIXED. Do not put an English
  description or numeric range into value.
- RANKING facts: include one for every time point with a unique visible highest
  category. subject is "highest", category is empty,
  timePoint is the literal time label, value is the highest category's exact label,
  unit is empty and direction is NOT_APPLICABLE. Omit a ranking when tied or unclear.
- Numeric data and trends already provide the basis for later comparisons. Do
  not add redundant prose COMPARISON facts, axis tick/range facts, color notes or
  document/footer notes to numerical charts.
- The title, units, categories and time points are already top-level fields;
  do not repeat them as TITLE_CONTEXT, UNIT, CATEGORY or TIME_POINT facts for
  numerical charts. Preserve every data point needed to describe the chart.
- For PROCESS or MAP, retain the visible process steps/features with their
  literal labels. Never force a numerical-chart convention onto a process/map.
- region is null when unnecessary, otherwise [x,y,width,height] in normalized
  coordinates wholly inside the image. Critical unreadable data must produce an
  ambiguous result, never a guessed number. Stop at the final JSON brace.
