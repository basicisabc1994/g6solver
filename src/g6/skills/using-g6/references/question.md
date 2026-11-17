## Question route

One question per task by default, kept across calls.

- **question.** Where the task may not decide a behaviour, `g6_confirm_behaviour` asks the person in the client's own
  dialog: one question per task by default, and the person may allow more or set none. Only the person answers; you
  never answer for them.

Where the task may not decide a behaviour, call `g6_confirm_behaviour` with the concrete scenario and its competing
outcomes. The person answers in the client's own dialog and may allow one more question; their own setting can
raise the default or set it to none. Only the person answers: no argument answers and you never answer for them.
Never assume an answer: an unanswered question stays open in the report.
