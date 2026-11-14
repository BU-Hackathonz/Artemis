<#
  Windows Credential Manager access for the launcher (dot-sourced by launch.ps1).
  Secrets are stored as generic credentials, persisted for this Windows user only. Windows protects
  them with DPAPI. Nothing here writes a file, and nothing prints a secret.

  If the C# helper cannot be compiled (AppLocker, antivirus, Constrained Language Mode), the
  launcher must still start: $script:CredStoreAvailable is $false and every function degrades to
  "nothing saved" so the caller falls back to typing the key.
#>

$script:CredStoreAvailable = $true
$script:CredStoreError = ""
try {
    if (-not ("ArtemisCred" -as [type])) {
        Add-Type -ErrorAction Stop -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class ArtemisCred {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL {
        public uint Flags; public uint Type; public string TargetName; public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize; public IntPtr CredentialBlob; public uint Persist;
        public uint AttributeCount; public IntPtr Attributes; public string TargetAlias; public string UserName;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredRead(string target, uint type, uint flags, out IntPtr cred);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredWrite(ref CREDENTIAL cred, uint flags);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredDelete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")]
    static extern void CredFree(IntPtr buffer);

    // CRED_TYPE_GENERIC = 1, CRED_PERSIST_LOCAL_MACHINE = 2 (this user, this machine).
    // Write and Delete return 0 on success, otherwise the Win32 error (1168 = not found).
    public static string Read(string target) {
        IntPtr p;
        if (!CredRead(target, 1, 0, out p)) return null;
        try {
            CREDENTIAL c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
            int size = (int)c.CredentialBlobSize;
            if (size == 0) return "";
            string secret = Marshal.PtrToStringUni(c.CredentialBlob, size / 2);
            // Wipe Windows' copy of the secret before handing the buffer back.
            Marshal.Copy(new byte[size], 0, c.CredentialBlob, size);
            return secret;
        } finally { CredFree(p); }
    }
    public static int Write(string target, string secret) {
        byte[] bytes = Encoding.Unicode.GetBytes(secret);
        IntPtr blob = Marshal.AllocHGlobal(bytes.Length);
        try {
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            CREDENTIAL c = new CREDENTIAL();
            c.Type = 1; c.TargetName = target; c.CredentialBlobSize = (uint)bytes.Length;
            c.CredentialBlob = blob; c.Persist = 2; c.UserName = "artemis";
            return CredWrite(ref c, 0) ? 0 : Marshal.GetLastWin32Error();
        } finally {
            Array.Clear(bytes, 0, bytes.Length);
            Marshal.Copy(new byte[bytes.Length], 0, blob, bytes.Length);
            Marshal.FreeHGlobal(blob);
        }
    }
    public static int Delete(string target) {
        return CredDelete(target, 1, 0) ? 0 : Marshal.GetLastWin32Error();
    }
}
"@
    }
} catch {
    $script:CredStoreAvailable = $false
    $script:CredStoreError = $_.Exception.Message
}

$script:ApiKeyTarget = "Artemis/AnthropicApiKey"
$script:WorkspaceTarget = "Artemis/AnthropicWorkspaceId"

# Returns the saved value, or $null when nothing is saved or the store is unavailable.
function Get-StoredSecret([string]$Target) {
    if (-not $script:CredStoreAvailable) { return $null }
    try { return [ArtemisCred]::Read($Target) } catch { return $null }
}

# Saves the value of the environment variable named $EnvName under $Target.
# Returns $true only when Windows confirmed the write; otherwise $false and $script:CredStoreError.
#
# It takes the variable's NAME, not the secret, on purpose. If PowerShell module logging is on
# (event 4103), Windows records the argument values of every function call, so a secret passed
# as an argument would land in the event log. The value is read here and goes straight into the
# .NET call, which is not logged.
function Save-StoredSecretFromEnv([string]$Target, [string]$EnvName) {
    if (-not $script:CredStoreAvailable) { return $false }
    if (-not [Environment]::GetEnvironmentVariable($EnvName)) {
        $script:CredStoreError = "$EnvName is not set"
        return $false
    }
    try { $rc = [ArtemisCred]::Write($Target, [Environment]::GetEnvironmentVariable($EnvName)) } catch { $script:CredStoreError = $_.Exception.Message; return $false }
    if ($rc -eq 0) { return $true }
    $script:CredStoreError = "Windows error $rc"
    return $false
}

# Returns "removed", "none" (nothing was saved: Windows error 1168) or "failed".
function Remove-StoredSecret([string]$Target) {
    if (-not $script:CredStoreAvailable) { return "failed" }
    try { $rc = [ArtemisCred]::Delete($Target) } catch { $script:CredStoreError = $_.Exception.Message; return "failed" }
    if ($rc -eq 0) { return "removed" }
    if ($rc -eq 1168) { return "none" }
    $script:CredStoreError = "Windows error $rc"
    return "failed"
}

# Saves what was just typed: the key if $KeyTyped, and the workspace ID if one is set.
# Returns @{ Ok; Message }, and the message says exactly what happened, because the key can be
# saved while the workspace ID is not. The values are handed over by environment variable name
# (see Save-StoredSecretFromEnv), never as arguments.
function Save-TypedSecrets([bool]$KeyTyped) {
    $keySaved = $false
    if ($KeyTyped) {
        $keySaved = Save-StoredSecretFromEnv $script:ApiKeyTarget "ANTHROPIC_API_KEY"
        if (-not $keySaved) {
            return @{ Ok = $false; Message = "Nothing was saved ($script:CredStoreError). You will be asked again next time." }
        }
    }
    if ($env:ANTHROPIC_WORKSPACE_ID -and -not (Save-StoredSecretFromEnv $script:WorkspaceTarget "ANTHROPIC_WORKSPACE_ID")) {
        if ($keySaved) {
            return @{ Ok = $false; Message = "The key was saved, but the workspace ID was not ($script:CredStoreError). You will be asked for the workspace ID again next time. Run Artemis.cmd -ForgetKey to remove the saved key." }
        }
        return @{ Ok = $false; Message = "The workspace ID was not saved ($script:CredStoreError). You will be asked for it again next time." }
    }
    return @{ Ok = $true; Message = "Saved. Run Artemis.cmd -ForgetKey to remove it." }
}
