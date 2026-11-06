# Excluded from xb_questions.jsonl

- W25 (template W3): the part is reached by 24 SKU records but only 23 distinct sku_code values, because the xb generator produced 7 duplicated sku_code values. "How many SKUs" is therefore ambiguous (records vs codes); the question was removed before any LLM run.
