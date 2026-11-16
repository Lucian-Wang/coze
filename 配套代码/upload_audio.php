<?php
/**
 * 简易上传接口：用于把本地转码后的音频文件（wav/mp3/ogg）上传到服务器，返回可公网访问的 URL。
 *
 * 特点：
 * - 只允许 wav/mp3/ogg（可扩展）
 * - 校验 API KEY（避免被人当成免费文件床）
 * - 限制文件大小
 * - 生成随机文件名，避免路径穿越
 * - 返回 JSON：{ "code":0, "data": { "url": "https://..." }, "msg":"ok" }
 *
 * 部署方式（示例）：
 * 1) 放到：/www/wwwroot/your-site/public/upload_audio.php
 * 2) 确保 UPLOAD_DIR 可写，且能通过浏览器访问 UPLOAD_PUBLIC_BASE 对应的 URL
 * 3) 用 Nginx/Apache 配好域名：https://your-domain.com/uploads/audio/...
 *
 * 安全建议：
 * - 一定要改默认的 UPLOAD_API_KEY
 * - 最好加 IP 白名单 / BasicAuth / WAF
 * - 建议对上传目录禁用 PHP 执行（Nginx location 禁止）
 */

header('Content-Type: application/json; charset=utf-8');

function respond($code, $msg, $data = null) {
    $resp = [
        "code" => $code,
        "msg" => $msg,
        "data" => $data === null ? (object)[] : $data
    ];
    echo json_encode($resp, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

// ========= 配置区（请按你的服务器改） =========

// 1) 上传鉴权 key：请求头 X-Upload-Key 或 query ?key=xxx
$UPLOAD_API_KEY = getenv("UPLOAD_API_KEY");
if ($UPLOAD_API_KEY === false || $UPLOAD_API_KEY === "") {
    // 强烈建议通过环境变量配置；这里给一个兜底，但请你部署时务必改掉
    $UPLOAD_API_KEY = "";
}

// 2) 物理落盘目录（必须可写）
$UPLOAD_DIR = getenv("UPLOAD_DIR");
if ($UPLOAD_DIR === false || $UPLOAD_DIR === "") {
    // 例：/www/wwwroot/your-site/public/uploads/audio
    $UPLOAD_DIR = __DIR__ . "/uploads/audio";
}

// 3) 文件公网访问 base URL（必须与你的站点配置一致）
$UPLOAD_PUBLIC_BASE = getenv("UPLOAD_PUBLIC_BASE");
if ($UPLOAD_PUBLIC_BASE === false || $UPLOAD_PUBLIC_BASE === "") {
    // 例：https://your-domain.com/uploads/audio
    $UPLOAD_PUBLIC_BASE = "https://your-domain.com/uploads/audio";
}

// 4) 文件大小限制（字节��
$MAX_BYTES = intval(getenv("UPLOAD_MAX_BYTES") ?: (20 * 1024 * 1024)); // 默认 20MB

// 5) 允许的音频扩展名与 MIME（只放你需要的）
$ALLOW_EXT = ["wav", "mp3", "ogg"];
$ALLOW_MIME = [
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/ogg",
    "application/ogg",
    "application/octet-stream", // 某些客户端不准，会用这个
];

// ========= 鉴权 =========
$headerKey = "";
if (function_exists("getallheaders")) {
    $headers = getallheaders();
    if (isset($headers["X-Upload-Key"])) $headerKey = $headers["X-Upload-Key"];
    if (isset($headers["x-upload-key"])) $headerKey = $headers["x-upload-key"];
}

$queryKey = isset($_GET["key"]) ? $_GET["key"] : "";
$key = $headerKey ?: $queryKey;

if ($key !== $UPLOAD_API_KEY) {
    respond(401, "Unauthorized");
}

// ========= 方法与文件 =========
if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    respond(405, "Method Not Allowed");
}

if (!isset($_FILES["file"])) {
    respond(400, "Missing file field: file");
}

$f = $_FILES["file"];
if (!is_array($f) || !isset($f["error"])) {
    respond(400, "Invalid upload");
}

if ($f["error"] !== UPLOAD_ERR_OK) {
    respond(400, "Upload error: " . strval($f["error"]));
}

$tmp = $f["tmp_name"];
$origName = isset($f["name"]) ? $f["name"] : "audio";
$size = intval(isset($f["size"]) ? $f["size"] : 0);

if ($size <= 0) {
    respond(400, "Empty file");
}

if ($size > $MAX_BYTES) {
    respond(413, "File too large");
}

// ========= 扩展名/类型校验 =========
$ext = strtolower(pathinfo($origName, PATHINFO_EXTENSION));
if ($ext === "") {
    respond(400, "File extension required (wav/mp3/ogg)");
}
if (!in_array($ext, $ALLOW_EXT, true)) {
    respond(415, "Unsupported file extension: " . $ext);
}

// MIME 检测（尽量使用 fileinfo）
$mime = "";
if (function_exists("finfo_open")) {
    $fi = finfo_open(FILEINFO_MIME_TYPE);
    if ($fi) {
        $mime = finfo_file($fi, $tmp);
        finfo_close($fi);
    }
}
if ($mime && !in_array($mime, $ALLOW_MIME, true)) {
    respond(415, "Unsupported mime: " . $mime);
}

// ========= 创建目录 =========
if (!is_dir($UPLOAD_DIR)) {
    if (!mkdir($UPLOAD_DIR, 0755, true)) {
        respond(500, "Failed to create upload dir");
    }
}
if (!is_writable($UPLOAD_DIR)) {
    respond(500, "Upload dir not writable");
}

// ========= 保存文件 =========
// 生成随机文件名：yyyyMMdd/uuid.ext
$dateDir = date("Ymd");
$subDir = rtrim($UPLOAD_DIR, "/") . "/" . $dateDir;
if (!is_dir($subDir)) {
    if (!mkdir($subDir, 0755, true)) {
        respond(500, "Failed to create date dir");
    }
}

$uuid = bin2hex(random_bytes(16));
$filename = $uuid . "." . $ext;
$destPath = $subDir . "/" . $filename;

if (!move_uploaded_file($tmp, $destPath)) {
    // 某些环境 move_uploaded_file 失败时，尝试 rename
    if (!@rename($tmp, $destPath)) {
        respond(500, "Failed to save file");
    }
}

// ========= 返回公网 URL =========
$publicBase = rtrim($UPLOAD_PUBLIC_BASE, "/");
$url = $publicBase . "/" . rawurlencode($dateDir) . "/" . rawurlencode($filename);

respond(0, "ok", ["url" => $url]);