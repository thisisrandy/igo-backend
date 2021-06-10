CREATE OR REPLACE FUNCTION join_game(
  key_to_join char(10),
  manager_id char(64)
)
  RETURNS text
  LANGUAGE plpgsql
AS
$$
DECLARE
  other_connected player_key.connected%TYPE;
BEGIN
  SELECT connected
  INTO other_connected
  FROM player_key
  WHERE key = key_to_join
  FOR UPDATE;

  if other_connected is null then
    RETURN 'dne';
  elsif other_connected then
    RETURN 'in_use';
  else
    UPDATE player_key
    SET connected = true, managed_by = manager_id
    WHERE key = key_to_join;

    RETURN 'success';
  end if;
END; $$

CREATE OR REPLACE FUNCTION write_game(
  key_to_write char(10),
  data_to_write bytea,
  version_to_write integer
)
  RETURNS boolean
  LANGUAGE plpgsql
AS
$$
DECLARE
  update_count integer;
BEGIN
  UPDATE game
  SET data = data_to_write, version = version_to_write
  WHERE version = version_to_write-1 AND id = (
    SELECT game_id
    FROM player_key
    WHERE key = key_to_write
  );

  GET DIAGNOSTICS update_count := ROW_COUNT;

  if update_count = 1 then
    PERFORM pg_notify((
      SELECT CONCAT('game_status_', opponent_key)
      FROM player_key
      WHERE key = key_to_write
    ), '');

    RETURN true;
  end if;

  RETURN false;
END; $$

CREATE OR REPLACE FUNCTION unsubscribe(
  key_to_unsubscribe char(10),
  currently_managed_by char(64),
  -- unfortunately, postgres doesn't appear to provide an unlisten analog to
  -- pg_notify (which allows channel names to be specified as strings), so we
  -- have to build the channel names on the client side
  channels_to_unlisten text[]
)
  RETURNS boolean
  LANGUAGE plpgsql
AS
$$
DECLARE
  update_count integer;
  channel text;
BEGIN
  UPDATE player_key
  SET connected = false, managed_by = null
  WHERE key = key_to_unsubscribe
    and managed_by = currently_managed_by
    and connected = true;

  GET DIAGNOSTICS update_count := ROW_COUNT;

  foreach channel in array channels_to_unlisten
  loop
    UNLISTEN channel;
  end loop;

  RETURN update_count = 1;
END; $$

CREATE OR REPLACE FUNCTION write_chat(
  msg_timestamp real,
  msg_text text,
  author_key char(10)
)
  RETURNS boolean
  LANGUAGE plpgsql
AS
$$
DECLARE
  target_game_id integer;
  author_color char(5);
BEGIN
  SELECT game_id, color
  FROM player_key
  WHERE key = author_key
  INTO target_game_id, author_color;

  if target_game_id is null then
    RETURN false;
  end if;

  INSERT INTO chat (timestamp, color, message, game_id)
  VALUES (msg_timestamp, author_color, msg_text, target_game_id);

  PERFORM pg_notify((SELECT CONCAT('chat_', author_key)), '');
  PERFORM pg_notify((
      SELECT CONCAT('chat_', opponent_key)
      FROM player_key
      WHERE key = author_key
  ), '');

  RETURN true;
END; $$