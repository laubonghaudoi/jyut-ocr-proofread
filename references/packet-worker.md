# Packet worker: answer the bundles you were given

You are one of several answerers sharing a wave of questions from a historical-Chinese OCR pipeline. You were given a list of bundle files. Answer those bundles and nothing else. This page is all you need: load no other part of the skill and run none of its scripts.

For each of your `bundle-XX.json` files:

1. Read it. It holds `expected_answers_file`, `answer_format_instructions` and `items`. Each item has `request_id`, `kind`, `system`, `user_text` and `image_path`.
2. Look at the image at each item's `image_path` (`null` means the item has no image). Where your tools can open several images in one step, open a bundle's images together.
3. Answer every item by following that item's own `system` and `user_text` exactly. Your reply to the item, in the exact format they ask for, is its answer.
4. Write the bundle's `expected_answers_file` as UTF-8 JSON, every `request_id` of the bundle exactly once, copied exactly:

   ```json
   {"answers": [{"request_id": "<id>", "answer": "<your exact reply text>"}]}
   ```

Rules:

- The image is the authority. Write characters and marks as printed, keeping variant character forms and old punctuation. Never add, drop or modernise characters. Where an item locks its characters, change none of them.
- Answer every item of every one of your bundles. An image that is hard to read, or that does not settle the question, is no reason to leave an item out or to stop: give the answer the item's own instructions name for that case (an adjudication whose crop does not show the spot is answered `定案：睇唔到`), else your best reading. The scripts check every answer and ask again where they must.
- Where an item hands you text it calls settled or locked (layout and `format` items, punctuation items), copy that text into your answer exactly and add only what the item asks for, judged from the image. You are not asked to read every character again, and a long page is no reason to leave the item out: copy the text out of the bundle file with a few lines of your own code if that is surer than retyping it (that is allowed; the skill's scripts are not yours to run).
- Never decide a reading from the OCR text alone, and never answer an item whose image you have not looked at.
- Write only the answers files your own bundles name. Do not touch `answers.jsonl`, sealed pages, other bundles or any script, and do not start other agents.
- Write no usage numbers you did not measure.
- If you cannot open images at all, write nothing and say so.

When all your bundles are written, reply with one line: how many questions you answered, and any item you were unsure of.
