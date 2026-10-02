DROP FUNCTION IF EXISTS aegis.release_llm_slot(integer, text);
DROP FUNCTION IF EXISTS aegis.renew_llm_slot(integer, text, integer);
DROP FUNCTION IF EXISTS aegis.acquire_llm_slot(text, integer, integer);
DROP TABLE IF EXISTS aegis.llm_slots;
DROP TABLE IF EXISTS aegis.llm_calls;
