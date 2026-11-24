-- Generated from the supplied robot log only; select the project database before running.
-- Confirmed open jaw at lift => failed. Other completed sequences => unverified.
-- Only existing success rows with these exact IDs are updated. No deletions.
START TRANSACTION;
-- axis6 at closure: 122.48
UPDATE `event` SET `result`='unverified', `error_code`=NULL WHERE `event_id`='5c76ff4a02e549399c154e66316d592e' AND `result`='success';
-- axis6 at closure: 122.81
UPDATE `event` SET `result`='unverified', `error_code`=NULL WHERE `event_id`='e9ef754f80b14567a35bd860cc30d81f' AND `result`='success';
-- axis6 at closure: 29.95
UPDATE `event` SET `result`='failed', `error_code`='GRIP_NOT_CLOSED' WHERE `event_id`='0c3ae85395324d5c9f278724c3246705' AND `result`='success';
-- axis6 at closure: 29.95
UPDATE `event` SET `result`='failed', `error_code`='GRIP_NOT_CLOSED' WHERE `event_id`='2200242045ab4e5fbbbaa9329a1c6334' AND `result`='success';
-- axis6 at closure: 121.91
UPDATE `event` SET `result`='unverified', `error_code`=NULL WHERE `event_id`='a4b9edf101d6436284458a223138463e' AND `result`='success';
-- axis6 at closure: 29.95
UPDATE `event` SET `result`='failed', `error_code`='GRIP_NOT_CLOSED' WHERE `event_id`='ca524f641d41426d8ddd664f5b74273f' AND `result`='success';
-- axis6 at closure: 29.95
UPDATE `event` SET `result`='failed', `error_code`='GRIP_NOT_CLOSED' WHERE `event_id`='eab19bfd75174d10ba07353842b7b830' AND `result`='success';
-- axis6 at closure: 29.95
UPDATE `event` SET `result`='failed', `error_code`='GRIP_NOT_CLOSED' WHERE `event_id`='3850eb47026b412898fabaf0d56c65e3' AND `result`='success';
-- axis6 at closure: 29.95
UPDATE `event` SET `result`='failed', `error_code`='GRIP_NOT_CLOSED' WHERE `event_id`='bbd96f0c51ae417ab44d5dbb375044da' AND `result`='success';
COMMIT;
