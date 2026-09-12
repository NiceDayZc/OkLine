# LINE Chrome API — Complete Endpoint Reference

Gateway: `https://line-chrome-gw.line-apps.com`  ·  App: `CHROMEOS	3.7.2	Chrome_OS	`

All Thrift endpoints are `POST /api/<path>` with a JSON **array of positional args** as the body.
Headers on gateway calls: `X-Line-Access`, `X-Line-Chrome-Version: 3.7.2` (+ `X-LAL`, `X-Hmac`).
`X-Line-Application` is **never** sent on gateway requests — the extension sets it
exclusively on private OBS resource fetches; `X-Line-ChannelToken` is gateway
`/api/timeline/`-only (plus `/r/myhome/` OBS URLs).

Total endpoints documented: **87**


## Authentication / Login / Identity

### `AuthService.loginV2`

- **Path:** `POST /api/talk/thrift/Talk/AuthService/loginV2`
- **Args (positional):**
  - `loginRequest` *(struct)* — LoginRequest. Single positional struct arg. Fields seen in default builder literal.
    - `.type` *(enum:LoginType)* FU enum: ID_CREDENTIAL=0, QRCODE=1, ID_CREDENTIAL_WITH_E2EE=2
    - `.identityProvider` *(enum:IdentityProvider)* AU enum, LINE=1
    - `.identifier` *(string)* = RSAKeyInfo.keynm for email login (the RSA key name)
    - `.password` *(string)* RSA-encrypted credential hex string from mT(); for QRCODE type this is the verifier-derived value, set empty otherwise
    - `.keepLoggedIn` *(bool)* default false
    - `.accessLocation` *(string)* default empty
    - `.systemName` *(string)* 'Whale' or 'Chrome' based on UA
    - `.certificate` *(string)* device certificate, default empty
    - `.verifier` *(string)* set when type=QRCODE (from pin-code/e2ee confirm step)
    - `.secret` *(string)* base64 AES-CBC-encrypted e2ee public key; only for ID_CREDENTIAL_WITH_E2EE
    - `.e2eeVersion` *(i32)* =1
    - `.modelName` *(string)* default empty
- **Body example:** `[{"type":0,"identityProvider":1,"identifier":"key_nm_abc","password":"3f1a...rsahex","keepLoggedIn":false,"accessLocation":"","systemName":"Chrome","certificate":"","verifier":"","secret":"","e2eeVersion":1,"modelName":""}]`
- **Returns:** LoginResult struct: {type:enum:LoginResultType(kU SUCCESS=1/REQUIRE_QRCODE=2/REQUIRE_DEVICE_CONFIRM=3/REQUIRE_SMS_CONFIRM=4), certificate:string, tokenV3IssueResult:struct, verifier:string, pinCode:string, displayMessage:string, metadata:E2EELoginMetadata}. On SUCCESS destructured as {certificate, tokenV3IssueResult}; on REQUIRE_* uses {verifier, pinCode}.

### `AuthService.logoutV2`

- **Path:** `POST /api/talk/thrift/Talk/AuthService/logoutV2`
- **Args:** _(none)_
- **Body example:** `[]`
- **Returns:** void (no return value used)

### `AuthService.confirmE2EELogin`

- **Path:** `POST /api/talk/thrift/Talk/AuthService/confirmE2EELogin`
- **Args (positional):**
  - `verifier` *(string)* — the login verifier obtained from the QR/pin-code verification step (arg n)
  - `deviceSecret` *(string)* — base64 (RR=btoa) of the hashed E2EE key chain (e2eeChannelGenerateHashKeyChainToConfirmE2EE output); used to confirm E2EE. Thrift field name not visible in bundle (positional only).
- **Body example:** `["verifier_token_xyz","c2VjcmV0SGFzaEJhc2U2NA=="]`
- **Returns:** string (a new verifier token; assigned to verifier and later passed to loginV2 with type=QRCODE)

### `TalkService.getRSAKeyInfo`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getRSAKeyInfo`
- **Args (positional):**
  - `provider` *(enum:IdentityProvider)* — AU enum, LINE=1 passed
- **Body example:** `[1]`
- **Returns:** RSAKeyInfo struct: {keynm:string (RSA key name/id -> becomes LoginRequest.identifier), nvalue:string (hex RSA modulus), evalue:string (hex RSA public exponent), sessionKey:string (length-prefixed into the encrypted blob)}

### `TalkService.getEncryptedIdentityV3`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getEncryptedIdentityV3`
- **Args:** _(none)_
- **Body example:** `[]`
- **Returns:** EncryptedIdentityV3 struct: {wrappedNonce:binary/base64, kdfParameter1:binary/base64, kdfParameter2:binary/base64}. Used to initialize the LTSM secure-storage key.

### `TalkService.acquireEncryptedAccessToken`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/acquireEncryptedAccessToken`
- **Args (positional):**
  - `feature` *(enum:FeatureType)* — OU enum: OBS_VIDEO=1, OBS_GENERAL=2, OBS_RINGBACK_TONE=3
- **Body example:** `[2]`
- **Returns:** string: a \x1e(record-sep)/\x1f(unit-sep)-delimited blob. VR(result) splits into rows/cols; encrypted access token = result[1][0]. Used as OBS encrypted access token for the given feature.

### `SecondaryQrCodeLoginService.createSession`

- **Path:** `POST /api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createSession`
- **Args (positional):**
  - `request` *(struct)* — Empty struct passed ({}). No fields observed; CreateSessionRequest appears to have no required fields.
- **Body example:** `[{}]`
- **Returns:** CreateSessionResponse struct: {authSessionId:string}

### `SecondaryQrCodeLoginService.createQrCode`

- **Path:** `POST /api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createQrCode`
- **Args (positional):**
  - `request` *(struct)* — CreateQrCodeRequest
    - `.authSessionId` *(string)* from createSession
- **Body example:** `[{"authSessionId":"sess_abc123"}]`
- **Returns:** CreateQrCodeResponse struct: {callbackUrl:string (QR URL; client appends ?secret=<base64 e2ee pubkey>&e2eeVersion=1), longPollingIntervalSec:i32, longPollingMaxCount:i32}

### `SecondaryQrCodeLoginService.verifyCertificate`

- **Path:** `POST /api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/verifyCertificate`
- **Args (positional):**
  - `request` *(struct)* — VerifyCertificateRequest
    - `.authSessionId` *(string)* from createSession
    - `.certificate` *(string)* locally stored device certificate for this session/email
- **Body example:** `[{"authSessionId":"sess_abc123","certificate":"cert_blob_xyz"}]`
- **Returns:** void/empty on success (throws on failure; failure -> createPinCode fallback). Error response data may carry {alertMessage}.

### `SecondaryQrCodeLoginService.createPinCode`

- **Path:** `POST /api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/createPinCode`
- **Args (positional):**
  - `request` *(struct)* — CreatePinCodeRequest
    - `.authSessionId` *(string)* from createSession
- **Body example:** `[{"authSessionId":"sess_abc123"}]`
- **Returns:** CreatePinCodeResponse struct: {pinCode:string (the PIN to display on the secondary device)}

### `SecondaryQrCodeLoginService.qrCodeLoginV2`

- **Path:** `POST /api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginService/qrCodeLoginV2`
- **Args (positional):**
  - `request` *(struct)* — QrCodeLoginRequest
    - `.systemName` *(string)* 'CHROMEOS'
    - `.modelName` *(string)* 'CHROME'
    - `.autoLoginIsRequired` *(bool)* false
    - `.authSessionId` *(string)* from createSession
- **Body example:** `[{"systemName":"CHROMEOS","modelName":"CHROME","autoLoginIsRequired":false,"authSessionId":"sess_abc123"}]`
- **Returns:** QrCodeLoginResult struct: {certificate:string (stored for future logins), metaData:E2EELoginMetadata{errorCode:enum:E2EELoginMetadataErrorCode(yd), keyId:i32, publicKey:base64, encryptedKeyChain:base64}, tokenV3IssueResult:struct{accessToken,refreshToken,durationUntilRefreshInSec,refreshApiRetryPolicy{initialDelayInMillis,maxDelayInMillis,multiplier,jitterRate},loginSessionId,tokenIssueTimeEpochSe

### `SecondaryQrCodeLoginPermitNoticeService.checkQrCodeVerified`

- **Path:** `POST /api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginPermitNoticeService/checkQrCodeVerified`
- **Args (positional):**
  - `request` *(struct)* — CheckQrCodeVerifiedRequest. Long-polling call: uses headers X-Line-Session-ID=authSessionId and X-LST=longPollingIntervalSec*1000.
    - `.authSessionId` *(string)* from createSession
- **Body example:** `[{"authSessionId":"sess_abc123"}]`
- **Returns:** void/empty on success (resolves when QR scanned/verified on primary device). HTTP 410 => timeout.

### `SecondaryQrCodeLoginPermitNoticeService.checkPinCodeVerified`

- **Path:** `POST /api/talk/thrift/LoginQrCode/SecondaryQrCodeLoginPermitNoticeService/checkPinCodeVerified`
- **Args (positional):**
  - `request` *(struct)* — CheckPinCodeVerifiedRequest. Long-polling call with X-Line-Session-ID=authSessionId and X-LST header.
    - `.authSessionId` *(string)* from createSession
- **Body example:** `[{"authSessionId":"sess_abc123"}]`
- **Returns:** void/empty on success (resolves when PIN entered/verified on primary device). HTTP 410 => timeout.

### `AuthService.tokenRefresh`

- **Path:** `POST /api/auth/tokenRefresh`
- **Args (positional):**
  - `body` *(struct)* — NOT thrift-array. Plain JSON object body {refreshToken}. Endpoint is /api/auth/tokenRefresh (not /api/talk/thrift/...).
    - `.refreshToken` *(string)* current refresh token from tokenV3IssueResult.refreshToken
- **Body example:** `{"refreshToken":"rt_current_xyz"}`
- **Returns:** Partial tokenV3IssueResult that is merged into the stored one: {accessToken:string, refreshToken:string, durationUntilRefreshInSec:string, refreshApiRetryPolicy:{initialDelayInMillis,maxDelayInMillis,multiplier,jitterRate}, loginSessionId:string, tokenIssueTimeEpochSec:string}. Retried per refreshApiRetryPolicy on AUTH_RETRY_REQUIRED.


## Messaging (send / receive / reactions)

### `Talk/TalkService.sendMessage`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/sendMessage`
- **Args (positional):**
  - `reqSeq` *(i32)* — Client-generated request sequence number. For sendMessage derived as Number(localId.replace('local-','')); elsewhere via HO().
  - `message` *(struct)* — The Message struct. Built by rP() (base) + per-type composer (aP text, iP sticker, oP contact, media). Sent AFTER client-side E2EE encryptMessage() which may add chunks + contentMetadata.e2eeVersion a
    - `.from` *(string)* Sender MID. Set to caller's own mid by builder (param t of rP).
    - `.to` *(string)* Destination MID (user/room/group/square chat id).
    - `.toType` *(enum:MIDType)* gU enum: USER=0,ROOM=1,GROUP=2,SQUARE=3,SQUARE_CHAT=4,SQUARE_MEMBER=5,BOT=6,SQUARE_THREAD=7. Derived from 'to' prefix via AT/ST/TT.
    - `.id` *(string)* Message id (i64-as-string). Client temp id ZO(HO()) for outgoing; server assigns final.
    - `.createdTime` *(i64)* String(RI()) = epoch millis at send time.
    - `.deliveredTime` *(i64)* Server-set delivery time (i64-as-string). Present on received messages; used in endMessageId/lastDeliveredMessageId.
    - `.text` *(string)* UTF-8 message text. Omitted/void for E2EE (moved into chunks) and for media.
    - `.contentType` *(enum:ContentType)* EU enum. NONE=0 for plain text; STICKER=7, IMAGE=1, VIDEO=2, AUDIO=3, FILE=14, CONTACT=13, LOCATION=15, CHATEVENT=18, FLEX=22, etc.
    - `.contentMetadata` *(map<string,string>)* Key/value metadata. Keys seen: MENTION (JSON of MENTIONEES), REPLACE, STICON_OWNERSHIP, STKPKGID/STKID/STKTXT/STKVER/STKOPT/STKHASH (sticker
    - `.hasContent` *(bool)* false for text/NONE; true for media/sticker/contact messages.
    - `.relatedMessageId` *(string)* For replies/forwards: id of the related (original) message. Deleted from struct in cP() forward path.
    - `.messageRelationType` *(enum:MessageRelationType)* wU enum: FORWARD=0,AUTO_REPLY=1,SUBORDINATE=2,REPLY=3. Set to REPLY when replying.
    - `.relatedMessageServiceCode` *(enum:ServiceCode)* yU enum: UNKNOWN=0,TALK=1,SQUARE=2. Set to TALK for talk replies.
    - `.location` *(struct)* Present for LOCATION content. Carried through E2EE (yL sets u.location). Field members (title/address/latitude/longitude/phone) NOT visible 
    - `.chunks` *(list<binary>)* E2EE ciphertext chunks. Populated by encryptMessage for letter-sealing; chunks[3]/chunks[4] carry sender/receiver keyIds. Empty/absent for n
    - `.sessionId` *(i32)* Set to 0 by base builder rP().
    - `.reactions` *(list<struct>)* Server-returned only: list of {fromUserMid, atMillis, reactionType:{predefinedReactionType|paidReactionType}}. Not sent by client.
- **Body example:** `[1718900000000, {"from":"u1111111111111111111111111111111111","to":"u2222222222222222222222222222222222","toType":0,"id":"0","createdTime":1718900000000,"text":"hello","contentType":0,"contentMetadata":{},"hasContent":false,"sessionId":0}]`
- **Returns:** Message struct (the persisted message with server-assigned id, createdTime, deliveredTime).

### `Talk/TalkService.unsendMessage`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/unsendMessage`
- **Args (positional):**
  - `reqSeq` *(i32)* — Client-generated via HO().
  - `messageId` *(string)* — i64-as-string id of the message to unsend (recall). e.id.
- **Body example:** `[1718900000001, "14000000000000001"]`
- **Returns:** void (errors mapped: MESSAGE_NOT_DESTRUCTIBLE, MESSAGE_NOT_FOUND).

### `Talk/TalkService.sendChatChecked`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/sendChatChecked`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO().
  - `consumer` *(string)* — Chat/messageBox MID (e). The chat being marked as checked/read.
  - `lastMessageId` *(string)* — i64-as-string id of last seen/read message (x.id).
  - `sessionId` *(i8)* — Passed as 0. Session/device id (small int).
- **Body example:** `[1718900000002, "u2222222222222222222222222222222222", "14000000000000050", 0]`
- **Returns:** void.

### `Talk/TalkService.sendChatRemoved`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/sendChatRemoved`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO().
  - `chatMid` *(string)* — Chat/messageBox MID to remove (e / o).
  - `lastMessageId` *(string)* — i64-as-string; lastDeliveredMessageId.messageId of the chat (v / c).
  - `sessionId` *(i8)* — Passed as 0.
- **Body example:** `[1718900000003, "u2222222222222222222222222222222222", "14000000000000050", 0]`
- **Returns:** void.

### `Talk/TalkService.setChatHiddenStatus`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/setChatHiddenStatus`
- **Args (positional):**
  - `request` *(struct)* — SetChatHiddenStatusRequest.
    - `.reqSeq` *(i32)* HO().
    - `.chatMid` *(string)* Chat/messageBox MID to hide/unhide.
    - `.lastMessageId` *(string)* i64-as-string; lastDeliveredMessageId of the chat at the time of the action.
    - `.hidden` *(bool)* true = hide chat, false = unhide.
- **Body example:** `[{"reqSeq":1718900000004,"chatMid":"u2222222222222222222222222222222222","lastMessageId":"14000000000000050","hidden":true}]`
- **Returns:** void.

### `Talk/TalkService.react`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/react`
- **Args (positional):**
  - `request` *(struct)* — ReactRequest.
    - `.reqSeq` *(i32)* HO().
    - `.messageId` *(string)* i64-as-string id of the message being reacted to (e.id).
    - `.reactionType` *(struct)* MessageReactionType union-like struct. One of: {predefinedReactionType: enum:PredefinedReactionType} (bU: NICE=2,LOVE=3,FUN=4,AMAZING=5,SAD=
- **Body example:** `[{"reqSeq":1718900000005,"messageId":"14000000000000050","reactionType":{"predefinedReactionType":3}}]`
- **Returns:** void (reaction applied; reflected via op sync).

### `Talk/TalkService.cancelReaction`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/cancelReaction`
- **Args (positional):**
  - `request` *(struct)* — CancelReactionRequest.
    - `.reqSeq` *(i32)* HO().
    - `.messageId` *(string)* i64-as-string id of the message whose reaction is removed (e.id).
- **Body example:** `[{"reqSeq":1718900000006,"messageId":"14000000000000050"}]`
- **Returns:** void.

### `Talk/TalkService.sendPostback`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/sendPostback`
- **Args (positional):**
  - `request` *(struct)* — SendPostbackRequest (template/flex button postback).
    - `.messageId` *(string)* i64-as-string id of the source message containing the postback action (e.id).
    - `.url` *(string)* The postback URL string (e.g. 'linepostback://postback?...' or built linepostback URL with _data/_mode params).
    - `.chatMID` *(string)* Chat MID where the message lives (XO(e, myMid)). NOTE the uppercase 'MID' spelling exactly as in code.
    - `.originMID` *(string)* Originator MID = e.from (the bot/sender of the source message). Uppercase 'MID'.
- **Body example:** `[{"messageId":"14000000000000050","url":"linepostback://postback?_data=abc&_mode=x","chatMID":"u2222222222222222222222222222222222","originMID":"u3333333333333333333333333333333333"}]`
- **Returns:** void (server may push a follow-up message).

### `Talk/TalkService.determineMediaMessageFlow`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/determineMediaMessageFlow`
- **Args (positional):**
  - `request` *(struct)* — DetermineMediaMessageFlowRequest.
    - `.chatMid` *(string)* Chat/peer MID for which to determine media (image/video/audio/file) message flow version.
- **Body example:** `[{"chatMid":"u2222222222222222222222222222222222"}]`
- **Returns:** struct {flowMap: map<enum:ContentType,i32(EncVersion V1=1/V2=2)>, cacheTtlMillis: i64}. flowMap keyed by IMAGE/VIDEO/AUDIO/FILE.

### `Talk/TalkService.getMessageReadRange`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getMessageReadRange`
- **Args (positional):**
  - `chatIds` *(list<string>)* — List of chat/messageBox MIDs to fetch read ranges for (e).
  - `syncReason` *(enum:SyncReason)* — DU enum (e.g. UNKNOWN=1, OPERATION=3). Passed as t.
- **Body example:** `[["c11111111111111111111111111111111","c22222222222222222222222222222222"], 1]`
- **Returns:** list<struct> each {chatId: string, ranges: map<memberMid, list<{startMessageId,endMessageId,startTime,endTime}>>}. Stored as messageReadRanges[chatId]={data}.

### `Talk/TalkService.getMessageBoxesByIds`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getMessageBoxesByIds`
- **Args (positional):**
  - `request` *(struct)* — GetMessageBoxesByIdsRequest.
    - `.messageBoxIds` *(list<string>)* MessageBox/chat MIDs to fetch (chunked by ~? in caller).
    - `.withUnreadCount` *(bool)* true to include unreadCount per box.
    - `.lastMessagesCount` *(i32)* Number of trailing messages to include per box (caller passes 1).
  - `syncReason` *(enum:SyncReason)* — DU enum (n).
- **Body example:** `[{"messageBoxIds":["u2222222222222222222222222222222222"],"withUnreadCount":true,"lastMessagesCount":1}, 3]`
- **Returns:** struct {messageBoxesByIds: map<id, MessageBox>}. MessageBox has {id, unreadCount, lastMessages:list<Message>, lastDeliveredMessageId:{deliveredTime,messageId}, lastSeenMessageId, ...}.

### `Talk/TalkService.getMessagesByIds`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getMessagesByIds`
- **Args (positional):**
  - `messageIds` *(list<string>)* — INFERRED (not seen in bundle): list of i64-as-string message ids to fetch. Could instead be wrapped in a request struct (e.g. {messageIds:[...]}). No call site to confirm — treat as unverified.
- **Body example:** `[["14000000000000050","14000000000000051"]]`
- **Returns:** Likely list<Message> (unverified).

### `Talk/TalkService.getMessageBoxes`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getMessageBoxes`
- **Args (positional):**
  - `request` *(struct)* — GetMessageBoxesRequest (paginated list of message boxes).
    - `.minChatId` *(string)* Pagination cursor: id of last box from previous page (undefined for first page).
    - `.activeOnly` *(bool)* true = only active chats.
    - `.unreadOnly` *(bool)* false to include read boxes too.
    - `.messageBoxCountLimit` *(i32)* Max boxes per page (caller uses config 'function.limit.sync.messageboxes' default 100).
    - `.withUnreadCount` *(bool)* true to include unreadCount.
    - `.lastMessagesPerMessageBoxCount` *(i32)* Trailing messages per box (caller passes 5).
  - `syncReason` *(enum:SyncReason)* — DU enum (e).
- **Body example:** `[{"minChatId":null,"activeOnly":true,"unreadOnly":false,"messageBoxCountLimit":100,"withUnreadCount":true,"lastMessagesPerMessageBoxCount":5}, 2]`
- **Returns:** struct {messageBoxes: list<MessageBox>, hasNext: bool}.

### `Talk/TalkService.getPreviousMessagesV2WithRequest`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getPreviousMessagesV2WithRequest`
- **Args (positional):**
  - `request` *(struct)* — GetPreviousMessagesV2Request - fetch messages older than endMessageId.
    - `.messageBoxId` *(string)* Chat/messageBox MID (e).
    - `.endMessageId` *(struct)* MessageBoxV2MessageId cursor: the message to page backwards from.
    - `.messagesCount` *(i32)* Number of previous messages to fetch (caller uses config 'limit.sync.messages' default 100).
  - `syncReason` *(enum:SyncReason)* — DU enum (n).
- **Body example:** `[{"messageBoxId":"u2222222222222222222222222222222222","endMessageId":{"messageId":"14000000000000050","deliveredTime":1718900000000},"messagesCount":100}, 3]`
- **Returns:** list<Message> (older messages, excluding the boundary message; client filters e.id !== endMessageId).

### `Talk/TalkService.getRecentMessagesV2`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getRecentMessagesV2`
- **Args (positional):**
  - `messageBoxId` *(string)* — Chat/messageBox MID (e).
  - `messagesCount` *(i32)* — Number of recent messages to fetch (s; defaults around 50).
- **Body example:** `["u2222222222222222222222222222222222", 50]`
- **Returns:** list<Message> (most recent messages for the box; client decrypts via decryptMessageList).

### `Talk/TalkService.getLastOpRevision`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getLastOpRevision`
- **Args:** _(none)_
- **Body example:** `[]`
- **Returns:** i64 (revision number as string) - the last operation revision; used as localRev for op sync.


## Contacts / Relations / Buddy

### `Talk/TalkService.findAndAddContactsByMid`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/findAndAddContactsByMid`
- **Args (positional):**
  - `reqSeq` *(i32)* — thrift convention; NOT observable — endpoint registered but result discarded in comma-expr, no call site
  - `type` *(enum:ContactType)* — inferred (addByMid variants take type+id+name); not observable in bundle
  - `ids` *(list<string>)* — inferred list of mids; not observable
- **Body example:** `[1234567, 0, ["u0123456789abcdef0123456789abcdef"]]`
- **Returns:** map<string,Contact> (inferred) — not observable

### `Talk/TalkService.getAllContactIds`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getAllContactIds`
- **Args (positional):**
  - `syncReason` *(enum:SyncReason)* — var DU; e.g. DU.FULL_SYNC=4, DU.INITIALIZATION=2. Call: vj({},e) where e is the SyncReason
- **Body example:** `[4]`
- **Returns:** list<string> (contact mids); response read as .contactList

### `Talk/TalkService.getContactsV2`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getContactsV2`
- **Args (positional):**
  - `request` *(struct)* — GetContactsV2Request
    - `.targetUserMids` *(list<string>)* the mids to look up (chunked, default chunk limit 100 from configurations[limit.sync.contacts])
    - `.neededContactCalendarEvents` *(list<?>)* passed as empty array [] in this client; element type unknown
  - `syncReason` *(enum:SyncReason)* — var DU; positional 2nd arg (from destructured syncReason:n). e.g. OPERATION=3, FULL_SYNC=4
- **Body example:** `[{"targetUserMids":["u0123456789abcdef0123456789abcdef"],"neededContactCalendarEvents":[]}, 4]`
- **Returns:** struct { contacts: map<string, ContactV2Wrapper> } where each value wrapper has field `contact` (Contact). Read as (await gj(...)).contacts then entry.contact and contact.status

### `Talk/TalkService.blockContact`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/blockContact`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO() counter
  - `id` *(string)* — target contact mid
- **Body example:** `[1234567, "u0123456789abcdef0123456789abcdef"]`
- **Returns:** void

### `Talk/TalkService.unblockContact`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/unblockContact`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO() counter
  - `id` *(string)* — target contact mid
  - `reference` *(string)* — always passed as empty string "" by this client
- **Body example:** `[1234567, "u0123456789abcdef0123456789abcdef", ""]`
- **Returns:** void

### `Talk/TalkService.getBlockedContactIds`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getBlockedContactIds`
- **Args (positional):**
  - `syncReason` *(enum:SyncReason)* — var DU; call wj({},e) where e is SyncReason
- **Body example:** `[4]`
- **Returns:** list<string> (blocked contact mids)

### `Talk/TalkService.findContactsByPhone`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/findContactsByPhone`
- **Args (positional):**
  - `phones` *(list<string>)* — phone numbers in international format with leading +; client passes [`+${countryNumber} ${searchText}`]
- **Body example:** `[["+81 9012345678"]]`
- **Returns:** map<string,Contact> — client does Object.values(result)[0] to get first Contact

### `Talk/TalkService.findContactByUserid`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/findContactByUserid`
- **Args (positional):**
  - `searchId` *(string)* — the LINE userid/ID string typed by the user
- **Body example:** `["someuserid123"]`
- **Returns:** Contact (single struct)

### `Talk/TalkService.updateContactSetting`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/updateContactSetting`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO() counter
  - `mid` *(string)* — target contact mid
  - `flag` *(enum:ContactSetting)* — var LU; e.g. LU.CONTACT_SETTING_FAVORITE=8, CONTACT_SETTING_CONTACT_HIDE=4, etc.
  - `value` *(string)* — string value for the setting; for FAVORITE toggle passed as String(true)/String(false) i.e. "true"/"false"
- **Body example:** `[1234567, "u0123456789abcdef0123456789abcdef", 8, "true"]`
- **Returns:** void

### `Talk/TalkService.getFavoriteMids`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getFavoriteMids`
- **Args:** _(none)_
- **Body example:** `[]`
- **Returns:** list<string> (favorite contact mids)

### `Talk/TalkService.blockRecommendation`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/blockRecommendation`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO() counter
  - `id` *(string)* — recommended contact mid to block
- **Body example:** `[1234567, "u0123456789abcdef0123456789abcdef"]`
- **Returns:** void

### `Talk/TalkService.getRecommendationIds`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getRecommendationIds`
- **Args (positional):**
  - `syncReason` *(enum:SyncReason)* — var DU; call Sj({},e) where e is SyncReason
- **Body example:** `[4]`
- **Returns:** list<string> (recommended contact mids); client also tracks newRecommendContactIdList

### `Talk/TalkService.getBlockedRecommendationIds`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getBlockedRecommendationIds`
- **Args (positional):**
  - `syncReason` *(enum:SyncReason)* — var DU; call Tj({},e) where e is SyncReason
- **Body example:** `[4]`
- **Returns:** list<string> (blocked recommendation mids)

### `Relation/RelationService.addFriendByMid`

- **Path:** `POST /api/talk/thrift/Relation/RelationService/addFriendByMid`
- **Args (positional):**
  - `request` *(struct)* — AddFriendByMidRequest
    - `.reqSeq` *(i32)* HO() counter
    - `.userMid` *(string)* target user's mid to add as friend
    - `.tracking` *(struct)* AddFriendTracking { reference: string (always "" in client), trackingMetaV2: struct }. trackingMetaV2 is a union-like struct with optional m
- **Body example:** `[{"reqSeq":1234567,"userMid":"u0123456789abcdef0123456789abcdef","tracking":{"reference":"","trackingMetaV2":{"friendRecommendation":{}}}}]`
- **Returns:** struct (AddFriendByMidResponse) — fields not referenced by client; on success client routes to friends list

### `Relation/RelationService.getTargetProfileNotice`

- **Path:** `POST /api/talk/thrift/Relation/RelationService/getTargetProfileNotice`
- **Args (positional):**
  - `request` *(struct)* — GetTargetProfileNoticeRequest
    - `.targetUserMid` *(string)* mid of the user whose profile notice is requested
- **Body example:** `[{"targetUserMid":"u0123456789abcdef0123456789abcdef"}]`
- **Returns:** struct { notice: { noticeAntiFraudDifferentRegion: bool, ... } } — client reads result.notice.noticeAntiFraudDifferentRegion (anti-fraud / different-region warning)

### `Talk/BuddyService.getBuddyDetail`

- **Path:** `POST /api/talk/thrift/Talk/BuddyService/getBuddyDetail`
- **Args (positional):**
  - `buddyMid` *(string)* — the official-account/buddy mid (s = profile.mid)
- **Body example:** `["u0123456789abcdef0123456789abcdef"]`
- **Returns:** struct BuddyDetail — cached under queryKey ['buddyDetail', mid]; field shape not referenced inline in this bundle


## Groups / Chats / Rooms

### `Talk/TalkService.createChat`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/createChat`
- **Args (positional):**
  - `request` *(struct)* — CreateChatRequest. Picture is NOT part of this struct - after success the code calls a separate upload (BD({},chatMid,picture)).
    - `.reqSeq` *(i32)* HO() counter, dedupe seq
    - `.type` *(enum:ChatType)* HU enum: GROUP=0, ROOM=1, PEER=2. UI sends GROUP or ROOM
    - `.name` *(string)* group/chat name; empty string allowed (t.trim()||"")
    - `.targetUserMids` *(list<string>)* member mids to add at creation (excludes self)
- **Body example:** `[{"reqSeq":12,"type":0,"name":"My Group","targetUserMids":["u1111111111111111111111111111111","u2222222222222222222222222222222"]}]`
- **Returns:** CreateChatResponse struct: { chat: Chat } where chat.chatMid is the new chat id (onSuccess destructures {chat:e}, reads e.chatMid)

### `Talk/TalkService.updateChat`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/updateChat`
- **Args (positional):**
  - `request` *(struct)* — UpdateChatRequest. The 'chat' field is the FULL existing Chat object spread with the changed field(s) overridden; updatedAttribute is a bitmask selecting which field(s) the server should apply.
    - `.reqSeq` *(i32)* HO() counter
    - `.chat` *(struct)* full Chat struct (see structs). Observed overrides: chatName (NAME), favoriteTimestamp (FAVORITE_TIMESTAMP), type (CHAT_TYPE)
    - `.updatedAttribute` *(enum:UpdateChatRequestAttribute)* MU bitmask: NAME=1, PICTURE_STATUS=2, PREVENTED_JOIN_BY_TICKET=4, NOTIFICATION_SETTING=8, INVITATION_TICKET=16, FAVORITE_TIMESTAMP=32, CHAT_
- **Body example:** `[{"reqSeq":13,"chat":{"chatMid":"c1111111111111111111111111111111","chatName":"New Name","type":0},"updatedAttribute":1}]`
- **Returns:** void / empty response (mutate result not read; cache updated optimistically)

### `Talk/TalkService.inviteIntoChat`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/inviteIntoChat`
- **Args (positional):**
  - `request` *(struct)* — InviteIntoChatRequest (single struct arg, distinct from inviteIntoRoom which is flat positional)
    - `.reqSeq` *(i32)* HO() counter
    - `.chatMid` *(string)* target group/chat mid (starts with 'C')
    - `.targetUserMids` *(list<string>)* user mids to invite
- **Body example:** `[{"reqSeq":14,"chatMid":"c1111111111111111111111111111111","targetUserMids":["u3333333333333333333333333333333"]}]`
- **Returns:** InviteIntoChatResponse (not read by caller); likely contains invited mids / failures

### `Talk/TalkService.deleteOtherFromChat`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/deleteOtherFromChat`
- **Args (positional):**
  - `request` *(struct)* — DeleteOtherFromChatRequest. Used to kick member(s).
    - `.reqSeq` *(i32)* HO() counter
    - `.chatMid` *(string)* group/chat mid
    - `.targetUserMids` *(list<string>)* mids to remove; UI passes a single-element list [t]
- **Body example:** `[{"reqSeq":15,"chatMid":"c1111111111111111111111111111111","targetUserMids":["u3333333333333333333333333333333"]}]`
- **Returns:** DeleteOtherFromChatResponse (not read)

### `Talk/TalkService.cancelChatInvitation`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/cancelChatInvitation`
- **Args (positional):**
  - `request` *(struct)* — CancelChatInvitationRequest. Cancels pending invitation(s).
    - `.reqSeq` *(i32)* HO() counter
    - `.chatMid` *(string)* group/chat mid
    - `.targetUserMids` *(list<string>)* invitee mids whose invitation to cancel; UI passes [t]
- **Body example:** `[{"reqSeq":16,"chatMid":"c1111111111111111111111111111111","targetUserMids":["u4444444444444444444444444444444"]}]`
- **Returns:** CancelChatInvitationResponse (not read)

### `Talk/TalkService.deleteSelfFromChat`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/deleteSelfFromChat`
- **Args (positional):**
  - `request` *(struct)* — DeleteSelfFromChatRequest. Leave a group/chat (no targetUserMids).
    - `.reqSeq` *(i32)* HO() counter
    - `.chatMid` *(string)* group/chat mid to leave
    - `.lastSeenMessages` *(list<struct>)* NOT observed in bundle - only reqSeq+chatMid are sent. May exist server-side but UI omits it. confidence low for this field
- **Body example:** `[{"reqSeq":17,"chatMid":"c1111111111111111111111111111111"}]`
- **Returns:** DeleteSelfFromChatResponse (not read)

### `Talk/TalkService.rejectChatInvitation`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/rejectChatInvitation`
- **Args (positional):**
  - `request` *(struct)* — RejectChatInvitationRequest
    - `.reqSeq` *(i32)* HO() counter
    - `.chatMid` *(string)* group/chat mid whose invitation to reject
- **Body example:** `[{"reqSeq":18,"chatMid":"c1111111111111111111111111111111"}]`
- **Returns:** RejectChatInvitationResponse (not read)

### `Talk/TalkService.acceptChatInvitation`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/acceptChatInvitation`
- **Args (positional):**
  - `request` *(struct)* — AcceptChatInvitationRequest
    - `.reqSeq` *(i32)* HO() counter
    - `.chatMid` *(string)* group/chat mid whose invitation to accept
- **Body example:** `[{"reqSeq":19,"chatMid":"c1111111111111111111111111111111"}]`
- **Returns:** AcceptChatInvitationResponse (not read)

### `Talk/TalkService.getAllChatMids`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getAllChatMids`
- **Args (positional):**
  - `request` *(struct)* — GetAllChatMidsRequest - flags selecting which mid lists to return
    - `.withMemberChats` *(bool)* include chats where you are a member
    - `.withInvitedChats` *(bool)* include chats you are invited to
  - `syncReason` *(enum:SyncReason)* — DU enum: UNSPECIFIED=0,UNKNOWN=1,INITIALIZATION=2,OPERATION=3,FULL_SYNC=4,AUTO_REPAIR=5,MANUAL_REPAIR=6,INTERNAL=7,USER_INITIATED=8. Passed as second positional arg.
- **Body example:** `[{"withMemberChats":true,"withInvitedChats":true},4]`
- **Returns:** GetAllChatMidsResponse: { memberChatMids: list<string>, invitedChatMids: list<string> } (destructured directly at top level in code)

### `Talk/TalkService.getChats`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getChats`
- **Args (positional):**
  - `request` *(struct)* — GetChatsRequest
    - `.chatMids` *(list<string>)* chat mids to fetch; code chunks by configurations['limit.sync.groups'] (default 100)
    - `.withMembers` *(bool)* populate extra.groupExtra.memberMids
    - `.withInvitees` *(bool)* populate extra.groupExtra.inviteeMids
- **Body example:** `[{"chatMids":["c1111111111111111111111111111111","c2222222222222222222222222222222"],"withMembers":true,"withInvitees":true}]`
- **Returns:** GetChatsResponse: { chats: list<Chat> } (see Chat struct: chatMid, chatName, type, picturePath, createdTime, favoriteTimestamp, extra.groupExtra.{memberMids,inviteeMids})

### `Talk/TalkService.inviteIntoRoom`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/inviteIntoRoom`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO() counter - positional arg 0
  - `roomMid` *(string)* — room mid (starts with 'R') - positional arg 1 (variable e)
  - `contactIds` *(list<string>)* — user mids to invite into the room - positional arg 2 (variable o)
- **Body example:** `[20,"r1111111111111111111111111111111",["u3333333333333333333333333333333"]]`
- **Returns:** void / empty (not read)

### `Talk/TalkService.leaveRoom`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/leaveRoom`
- **Args (positional):**
  - `reqSeq` *(i32)* — HO() counter - positional arg 0
  - `roomMid` *(string)* — room mid to leave (starts with 'R') - positional arg 1
- **Body example:** `[21,"r1111111111111111111111111111111"]`
- **Returns:** void / empty (not read)

### `Talk/TalkService.getRoomsV2`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getRoomsV2`
- **Args (positional):**
  - `roomMids` *(list<string>)* — single positional arg: list of room mids to fetch. Code chunks by configurations['limit.sync.groups'] (default 100). Called as Vj(config, roomMids).
- **Body example:** `[["r1111111111111111111111111111111","r2222222222222222222222222222222"]]`
- **Returns:** list<Room> (returned directly as an array). Room struct: { mid: string, memberMids: list<string>, ... } (other Room fields not individually visible in bundle)


## Profile / Settings / Configuration / Abuse

### `TalkService.getProfile`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/getProfile`
- **Args (positional):**
  - `syncReason` *(enum:SyncReason)* — DU enum. e.g. DU.INITIALIZATION(2), DU.OPERATION(3), DU.FULL_SYNC(4). Single positional arg after config.
- **Body example:** `[2]`
- **Returns:** struct Profile (confirmed fields mid, regionCode; full schema: userid, phoneticName, pictureStatus, displayName, statusMessage, picturePath, allowSearchByUserid, allowSearchByEmail, email, musicProfile, avatarProfile, ...)

### `TalkService.updateProfileAttributes`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/updateProfileAttributes`
- **Args (positional):**
  - `reqSeq` *(i32)* — request sequence number from HO()
  - `request` *(struct)* — ProfileAttributesRequest
    - `.profileAttributes` *(map<enum:ProfileAttribute, ProfileAttributeValue>)* map keyed by CU/ProfileAttribute enum value; value is {value:string, meta:map<string,string>}
- **Body example:** `[1,{"profileAttributes":{"2":{"value":"New Display Name","meta":{}}}}]`
- **Returns:** void (no return value read by client)

### `TalkService.getSettings`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/getSettings`
- **Args (positional):**
  - `syncReason` *(enum:SyncReason)* — DU enum. Single positional arg after config (hj(void 0, e) where e is DU value).
- **Body example:** `[2]`
- **Returns:** struct Settings (e.g. notificationDisabledWithSub:bool, e2eeEnable:bool, privacyReceiveMessagesFromNotFriend:bool, privacyAgeResult:enum, privacyAgeResultReceived:bool, plus notification* fields)

### `TalkService.updateSettingsAttributes2`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/updateSettingsAttributes2`
- **Args (positional):**
  - `reqSeq` *(i32)* — request sequence from HO()
  - `attributesToUpdate` *(list<enum:SettingsAttribute>)* — list of TU/SettingsAttribute enum values indicating which settings fields are being updated
  - `settings` *(struct)* — Settings struct carrying the new values for the attributes listed in arg[1]
    - `.privacyReceiveMessagesFromNotFriend` *(bool)* set when PRIVACY_RECV_MESSAGES_FROM_NOT_FRIEND(25) in list
    - `.notificationDisabledWithSub` *(i64/bool)* set when NOTIFICATION_DISABLED_WITH_SUB(16); UI writes 0 or Number.MAX_SAFE_INTEGER
    - `.e2eeEnable` *(bool)* set when E2EE_ENABLE(33)
    - `.privacyAgeResult` *(enum:AgeCheckResult)* SU enum
    - `.privacyAgeResultReceived` *(bool)* 
- **Body example:** `[1,[25],{"privacyReceiveMessagesFromNotFriend":false}]`
- **Returns:** void

### `TalkService.getSettingsAttributes2`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/getSettingsAttributes2`
- **Args (positional):**
  - `attributesToGet` *(list<enum:SettingsAttribute>)* — list of TU/SettingsAttribute enum values to fetch; single positional arg after config (fU({}, t) where t is number[])
- **Body example:** `[[16,33,25,60,61]]`
- **Returns:** struct Settings subset, e.g. {notificationDisabledWithSub, e2eeEnable, privacyReceiveMessagesFromNotFriend, privacyAgeResult, privacyAgeResultReceived}

### `TalkService.getConfigurations`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/getConfigurations`
- **Args (positional):**
  - `arg0` *(string)* — passed as empty string "". Likely a revision/etag or unused string param.
  - `arg1` *(string)* — passed as empty string "".
  - `arg2` *(string)* — passed as empty string "".
  - `region` *(string)* — region code from Profile.regionCode (e.g. 'JP','TH'). CONFIRMED value position.
  - `arg4` *(string)* — passed as empty string "" (likely carrier/phone).
  - `syncReason` *(enum:SyncReason)* — DU enum value. CONFIRMED last positional arg.
- **Body example:** `["","","","JP","",2]`
- **Returns:** struct Configurations (server-driven feature config map/list; compared via lodash isEqual in reducer)

### `TalkService.getServerTime`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/getServerTime`
- **Args:** _(none)_
- **Body example:** `[]`
- **Returns:** i64 server time (epoch seconds/millis); Number(await Hj()) used to sync token refresh clock

### `TalkService.reportAbuseEx`

- **Path:** `POST /api//api/talk/thrift/Talk/TalkService/reportAbuseEx`
- **Args (positional):**
  - `request` *(struct)* — ReportAbuseExRequest
    - `.abuseReportEntry` *(struct:AbuseReportEntry)* contains a single 'message' arm = AbuseMessageReport struct with reportSource, applicationType, spammerReasons(list<SpammerReason>), abuseMe
- **Body example:** `[{"abuseReportEntry":{"message":{"reportSource":7,"applicationType":368,"spammerReasons":[5],"abuseMessages":[],"metadata":{"userMid":"u1234...","displayName":"Bad User","statusMessage":"","picturePath":"/path"}}}}]`
- **Returns:** void


## End-to-End Encryption keys

### `TalkService.getE2EEPublicKey`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getE2EEPublicKey`
- **Args (positional):**
  - `mid` *(string)* — Target user/own mid whose E2EE public key is requested. Call site: zj(void 0, e, t, n) where e=mid.
  - `keyVersion` *(i32)* — E2EE public key version. Caller aM(e,t,n) passes t (e.g. 1 in negotiateUserPublicKey path; aM(e,1,t) is used for user keys).
  - `keyId` *(i32)* — Specific key id to fetch. 3rd positional arg n.
- **Body example:** `["u1234567890abcdef1234567890abcdef", 1, 5]`
- **Returns:** E2EEPublicKey struct: {version:i32, keyId:i32, keyData:binary(base64), createdTime:i64-as-string}. Code: const i=await zj(void 0,e,t,n); checks i.createdTime, stores i.keyData. keyData is base64-decoded via NR() before use.

### `TalkService.negotiateE2EEPublicKey`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/negotiateE2EEPublicKey`
- **Args (positional):**
  - `mid` *(string)* — Target user mid to negotiate an E2EE public key with. Call: qj(void 0, e).
- **Body example:** `["u1234567890abcdef1234567890abcdef"]`
- **Returns:** E2EENegotiationResult struct: {publicKey: E2EEPublicKey, allowedTypes: list<i32>(message contentTypes that may be encrypted), specVersion: i32}. Destructured as const{publicKey:t,allowedTypes:n,specVersion:r}=await qj(void 0,e).

### `TalkService.getE2EEPublicKeysEx`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getE2EEPublicKeysEx`
- **Args (positional):**
  - `ignoreE2EEGroupKey` *(bool)* — Boolean flag; observed value !1 (false) at the only call site Gj(void 0,!1,DU.UNKNOWN). Exact thrift field name not in bundle (positional only); likely a flag controlling whether group keys are includ
  - `syncReason` *(enum:SyncReason)* — DU enum (SyncReason): UNSPECIFIED=0, UNKNOWN=1, INITIALIZATION=2, OPERATION=3, FULL_SYNC=4, AUTO_REPAIR=5, MANUAL_REPAIR=6, INTERNAL=7, USER_INITIATED=8. Call passes DU.UNKNOWN (1).
- **Body example:** `[false, 1]`
- **Returns:** list<E2EEPublicKey> (the caller's OWN public keys). Result is sorted by keyId: (await Gj(void 0,!1,DU.UNKNOWN)).sort(((e,t)=>t.keyId-e.keyId)); compared against local key ids. Each item has .keyId (i32).

### `TalkService.registerE2EEGroupKey`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/registerE2EEGroupKey`
- **Args (positional):**
  - `version` *(i32)* — Protocol/key version. Call passes literal 1: Kj(void 0,1,e,r,a,i).
  - `chatMid` *(string)* — Group/chat mid to register the shared group key for. 2nd arg e (an EMPTY_GROUP check guards it).
  - `members` *(list<string>)* — List of member mids. Built as r=[]; r.push(s) for each [s,l] of group member->E2EEPublicKey entries.
  - `keyIds` *(list<i32>)* — Parallel list of each member's public keyId. a=[]; a.push(l.keyId).
  - `encryptedSharedKeys` *(list<binary>)* — Parallel list of the group shared key wrapped/encrypted for each member (base64). i.push(RR(await BA().e2eeChannelWrapGroupSharedKey(channel, sharedKey))). RR = base64-encode.
- **Body example:** `[1, "cab1234567890abcdef1234567890abcd", ["u1111111111111111111111111111111111","u2222222222222222222222222222222222"], [3, 4], ["BASE64WRAPPEDKEY1==","BASE64WRAPPEDKEY2=="]]`
- **Returns:** E2EEGroupSharedKey struct (the newly registered group key), passed to unwrapGroupKey: {keyVersion:i32, groupKeyId:i32, creator:string(mid), creatorKeyId:i32, receiver:string(mid), receiverKeyId:i32, encryptedSharedKey:binary(base64), allowedTypes:list<i32>, specVersion:i32}. On error code E2EE_RECREATE_GROUP_KEY it retries registerGroupKey.

### `TalkService.getE2EEGroupSharedKey`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getE2EEGroupSharedKey`
- **Args (positional):**
  - `version` *(i32)* — Protocol/key version; call passes literal 1: Wj(void 0,1,e,t).
  - `chatMid` *(string)* — Group/chat mid. 2nd arg e.
  - `groupKeyId` *(i32)* — Specific group key id to fetch. 3rd arg t (fetchGroupKey(e,t)).
- **Body example:** `[1, "cab1234567890abcdef1234567890abcd", 7]`
- **Returns:** E2EEGroupSharedKey struct {keyVersion:i32, groupKeyId:i32, creator:string, creatorKeyId:i32, receiver:string, receiverKeyId:i32, encryptedSharedKey:binary(base64), allowedTypes:list<i32>, specVersion:i32}. Passed to unwrapGroupKey.

### `TalkService.getLastE2EEGroupSharedKey`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getLastE2EEGroupSharedKey`
- **Args (positional):**
  - `version` *(i32)* — Protocol/key version; call passes literal 1: Qj(void 0,1,e).
  - `chatMid` *(string)* — Group/chat mid whose latest group shared key is requested. 2nd arg e.
- **Body example:** `[1, "cab1234567890abcdef1234567890abcd"]`
- **Returns:** E2EEGroupSharedKey struct (latest) {keyVersion:i32, groupKeyId:i32, creator:string, creatorKeyId:i32, receiver:string, receiverKeyId:i32, encryptedSharedKey:binary(base64), allowedTypes:list<i32>, specVersion:i32}. On NOT_FOUND error caller falls back to registerGroupKey.

### `TalkService.getLastE2EEPublicKeys`

- **Path:** `POST /api/talk/thrift/Talk/TalkService/getLastE2EEPublicKeys`
- **Args (positional):**
  - `chatMid` *(string)* — Group/chat mid; fetches the latest E2EE public key of every member. Single arg: Yj(void 0, e).
- **Body example:** `["cab1234567890abcdef1234567890abcd"]`
- **Returns:** map<string,E2EEPublicKey> (mid -> latest E2EEPublicKey). Iterated as for(const[n,r] of Object.entries(t)) this.addUserPublicKey(n,r). Each E2EEPublicKey: {version:i32, keyId:i32, keyData:binary(base64), createdTime:i64-string}.


## Channel token / Sticker shop / OBS media / Operations

### `ChannelService.issueChannelToken`

- **Path:** `POST /api/talk/thrift/Talk/ChannelService/issueChannelToken`
- **Args (positional):**
  - `channelId` *(string)* — The channel ID string. Hardcoded as "1341209850" in this build (the internal LINE Timeline/MyHome channel) - this is the value passed when the app needs a channel access token for /api/timeline/* and 
- **Body example:** `["1341209850"]`
- **Returns:** ChannelToken struct. Consumed in code only via .channelAccessToken (string) and .expiration (i64 epoch seconds, compared with Number(...) < now). Per LINE thrift IDL the full struct is ChannelToken{1: string token, 2: string channelAccessToken, 3: i64 expiration, 4: string refreshToken}. Code: getChannelAccessToken(){ if(!channelToken || Number(channelToken.expiration)<now) await renewChannelToken

### `ShopService.getOwnedProductSummaries`

- **Path:** `POST /api/shop/thrift/ShopService/ShopService/getOwnedProductSummaries`
- **Args (positional):**
  - `shopId` *(string)* — Shop identifier string. Values seen: "stickershop" (const tj) and "sticonshop" (const nj). Selects which catalogue to page through.
  - `offset` *(i32)* — Paging offset; loop starts at 0 and increments by 1000 (the limit) until offset+returned >= totalSize.
  - `limit` *(i32)* — Page size. Hardcoded 1000 (1e3) in the loop.
  - `displayInfo` *(struct)* — ShopProductDisplayInfo / locale struct describing language & country for localized product info.
- **Body example:** `["stickershop", 0, 1000, {"language": "ja", "country": "JP"}]`
- **Returns:** OwnedProductSummaries-style struct. Destructured as {productList, offset, totalSize}. productList: list<ProductSummary>. Each ProductSummary has: id(string), name(string), validUntil(i64/string), createdTime, latestVersion, and productTypeSummary{stickerSummary?, sticonSummary?, themeSummary?}. stickerSummary has: stickerResourceType(enum:$D STATIC=1/ANIMATION=2/SOUND=3/...), stickerHash(string), 

### `ShopService.setCustomizedImageText`

- **Path:** `POST /api/shop/thrift/ShopService/ShopService/setCustomizedImageText`
- **Args (positional):**
  - `request` *(struct)* — Single request struct identical in shape to the previewCustomizedImageText request: {productType, productId, nameRequestEntry}. Persists the customized name text for an owned sticker package.
- **Body example:** `[{"productType": 1, "productId": "12345", "nameRequestEntry": {"text": "Alice"}}]`
- **Returns:** Response struct destructured as {nameTextProperty}. nameTextProperty{plainText, encryptedText, nameTextMaxCharacterCount, status}. status is enum ej; success requires status===ej.OK (0). On success the app stores nameTextProperty into the local sticker summary. ej enum: OK=0, PRODUCT_UNSUPPORTED=1, TEXT_NOT_SPECIFIED=2, TEXT_STYLE_UNAVAILABLE=3, CHARACTER_COUNT_LIM(IT...)=4...

### `ShopService.previewCustomizedImageText`

- **Path:** `POST /api/shop/thrift/ShopService/ShopService/previewCustomizedImageText`
- **Args (positional):**
  - `request` *(struct)* — Single request struct: {productType, productId, nameRequestEntry}. Returns a preview (does not persist) of how the custom name text would render.
- **Body example:** `[{"productType": 1, "productId": "12345", "nameRequestEntry": {"text": "Alice"}}]`
- **Returns:** Response struct destructured as {nameTextProperty}; the handler returns just nameTextProperty. nameTextProperty{plainText(string), encryptedText(string/binary - used as imageText to render preview), nameTextMaxCharacterCount(i32), status(enum:ej)}.

### `Operation (SSE receive).receive`

- **Path:** `POST /api/operation/receive`
- **Args (positional):**
  - `version` *(string (query param))* — App version, "3.7.2", set on transport.query before connect.
  - `fullSyncRequestReason` *(string (query param))* — Optional reason for requesting a full sync on (re)connect; deleted from query in finally block after connect.
  - `lastPartialFullSyncs` *(string (query param, JSON))* — JSON.stringify(this.lastPartialFullSyncs): map of category->timestamp used to bound partial full-sync windows.
  - `localRev` *(string/i64 (query param))* — Local revision pointer. Updated to nextRevision from FULL_SYNC events: transport.query.localRev = nextRevision. This is the revision/fetchOps cursor.
  - `language` *(string (query param))* — The X-LAL underscore form of the locale (e.g. `en_US`, `ja_JP`), from the bundle's Up map.
  - `legyHost` *(string (query param, optional))* — Set when FR().legyHost is present (LEGY routing host).
- **Body example:** `GET https://line-chrome-gw.line-apps.com/api/operation/receive?version=3.7.2&lastPartialFullSyncs=%7B%7D&localRev=<rev>   (withCredentials:true; cookie 'lct' used; Accept: text/event-stream)`
- **Returns:** Server-Sent Events stream. customEvents handled: ping (connInfo keepalive, PingInterceptor range [20000,20000] step 0 spare 10000 -> expect ~20s ping), connInfoRevision (event 'connInfoRevision', data=Number revision -> FA.revision), reconnect (triggers reconnect), talkException (data=JSON TalkException -> handled/possibly kickout), fullSync (data={reasons:list, nextRevision} -> sets localRev=next

### `Operation (long-poll LF1).LF1`

- **Path:** `POST /api/talk/long-polling/LF1`
- **Args (positional):**
  - `X-Line-Session-ID` *(string (header))* — Session id passed as header. Required.
  - `X-LST` *(i32 (header, ms))* — Long-poll/Service timeout in milliseconds = rH = 110000 (1.1e5).
- **Body example:** `GET https://line-chrome-gw.line-apps.com/api/talk/long-polling/LF1  headers: { X-Line-Session-ID: <sessionId>, X-LST: 110000 }`
- **Returns:** response.result.metadata (returned to caller). Used in the secondary-device QR/pin login flow (returns the metadata string for the just-completed long-poll step). retryCount default.

### `Operation (long-poll JQ).JQ`

- **Path:** `POST /api/talk/long-polling/JQ`
- **Args (positional):**
  - `X-Line-Session-ID` *(string (header))* — Session id header. Required.
  - `X-LST` *(i32 (header, ms))* — Long-poll timeout in milliseconds = 180000 (1.8e5).
- **Body example:** `GET https://line-chrome-gw.line-apps.com/api/talk/long-polling/JQ  headers: { X-Line-Session-ID: <sessionId>, X-LST: 180000 }  (retryCount:0)`
- **Returns:** response.result.verifier (returned to caller). Part of the secondary login / pin verification long-poll; yields the verifier string. retryCount:0 (no retry).

### `Lan.notice`

- **Path:** `GET /api/lan/notice`
- **Args (positional):**
  - `lang` *(string (query param))* — Locale for the notices (e.g. `en`).
  - `country` *(string (query param))* — Country code (e.g. `JP`).
  - `nextSeq` *(i64 (query param))* — Paging cursor; start at 0 and pass the response's `nextSeq` for the next page.
  - `includeBody` *(bool (query param))* — Always `true` in the extension.
- **Body example:** `GET https://line-chrome-gw.line-apps.com/api/lan/notice?lang=en&country=JP&nextSeq=0&includeBody=true`
- **Returns:** `{documents: list<service notice/banner documents>, nextSeq}` — localized service notices fetched before/after login (filtered client-side on `extras.showTimingWhenLogin`). Exposed as `api.ops.lan_notice(lang=..., country=..., next_seq=...)`.

### `OBS.uploadProfile`

- **Path:** `POST /api/obs/uploadProfile`
- **Args (positional):**
  - `mid` *(string (query param))* — Target mid (user/group) - URL: /api/obs/uploadProfile?mid=<encodeURIComponent(mid)>. e.g. a freshly-created group chatMid or own profile mid.
  - `file` *(binary (Blob, request body))* — Raw image Blob is the POST body. content-type header is set to file.type (the Blob MIME). Produced via qk(image).
- **Body example:** `POST https://line-chrome-gw.line-apps.com/api/obs/uploadProfile?mid=u1234...  headers:{content-type: image/jpeg}  body=<image bytes>`
- **Returns:** OBS upload result (not destructured at call sites; awaited for side-effect of setting profile/group picture). Auth via gateway: X-Line-Access. Note this goes through the chrome_gw axios instance (zU), so it inherits X-Line-Chrome-Version:3.7.2 and X-Line-Access headers.

### `OBS.copyForMessage`

- **Path:** `POST /api/obs/copyForMessage`
- **Args (positional):**
  - `request` *(struct (request body, JSON object))* — Single JSON object body (NOT a thrift positional array - posted directly). Copies an existing OBS object so it can be re-sent as a message attachment.
- **Body example:** `POST https://line-chrome-gw.line-apps.com/api/obs/copyForMessage  body={"srcPath":{"service":"talk","sid":"<SID>","oid":"<OID>"},"dstPath":{"service":"talk","sid":"<SID>","oid":"reqid-abc123"},"toMid":"u1234...","isOriginal":false,"reqSeq":42,"talkMeta":"<meta>"}`
- **Returns:** Result with .gid (new group id) and .oid (resulting object id). On success app may updateGroupId(oldGroupId->v.gid). Auth via gateway X-Line-Access (zU instance).

### `OBS (direct resource).uploadResource / getObjectInfo / playback (X-Obs-Params + X-Talk headers)`

- **Path:** `POST /api/obs (DI().getServerBaseUrl('obs')) /r/<service>/<sid>/<oid>`
- **Args (positional):**
  - `service` *(string)* — OBS service segment (e.g. 'talk','myhome').
  - `sid` *(string)* — Storage id segment.
  - `oid` *(string)* — Object id segment.
  - `file` *(binary (Blob))* — Upload body (POST).
  - `xObsParams` *(string (header X-Obs-Params))* — Base64/encoded OBS params header.
  - `offset` *(i64)* — Chunk offset; produces Range header `bytes <offset>-<size-1>/<size>`.
- **Body example:** `POST https://obs.line-apps.com/r/talk/<sid>/<oid>  headers:{ X-Obs-Params:<params>, range: bytes 0-1023/1024, [headerMapper FD adds auth] }`
- **Returns:** OBS object headers/blob. Header auth (FD headerMapper): for URLs containing /r/<channelHost>/ (e.g. /r/myhome/) it sets X-Line-ChannelToken=<channelAccessToken from issueChannelToken('1341209850')>; otherwise sets X-Line-Access=<encrypted access token getEncryptedAccessToken(OBS_GENERAL)> AND X-Line-Application="CHROMEOS\t3.7.2\tChrome_OS\t". Download paths additionally set X-Talk-Meta (lB(messageId): a base64(JSON({message: base64(thrift blob)})) wrapper — see `okline.obs.build_talk_meta`). Public OBS GETs carry no auth header at all (headerMapper void).

### `OBS (resource info).info.obs`

- **Path:** `GET <obs-base>/<path>/info.obs`
- **Args (positional):**
  - `<path>` *(string (URL path))* — The raw OBS object path (`/r/<service>/<sid>/<oid>`).
- **Body example:** `GET https://obs.line-apps.com/r/talk/<sid>/<oid>/info.obs  [FD headerMapper auth]`
- **Returns:** Resource info for the object (used to check it before downloading). Same FD auth as the direct-resource endpoint; exposed as `api.obs.resource_info(path)`.

### `OBS (playback).playback.obs`

- **Path:** `GET <obs-base>/<path>/playback.obs`
- **Args (positional):**
  - `modelName` *(string (query param))* — Always `"CHROMEOS"` in the extension.
  - `networkType` *(string (query param))* — Always `"WiFi"`.
  - `lang` *(string (query param))* — Playback locale (short form: `en`, `ja`, `ko`, `zh-Hans`, `zh-Hant`; derived from the configured locale).
  - `X-Talk-Meta` *(string (header, optional))* — The thrift messageId blob (lB), required for E2EE (`/r/talk/em`) paths.
- **Body example:** `GET https://obs.line-apps.com/r/talk/m/<mid>/<oid>/playback.obs?modelName=CHROMEOS&networkType=WiFi&lang=en`
- **Returns:** Video playback info (stream/track metadata). FD auth; exposed as `api.obs.playback_info(path, message_id=...)`.

