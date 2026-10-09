# Run the actual production numeric-title collector with mock UIA objects.
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'inspect_controls.ps1') -Raw -Encoding UTF8
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0].Message }
$fn=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'ReadProfileNumberReport'},$true))
if ($fn.Count -ne 1) { throw 'Production number collector is missing' }
Invoke-Expression $fn[0].Extent.Text
Add-Type -TypeDefinition @'
namespace System.Windows.Automation {
    public enum TreeScope { Descendants }
    public enum ControlType { Text, Group }
    public class AutomationElement { public static object ClassNameProperty = new object(); }
    public class PropertyCondition {
        public object ClassName;
        public PropertyCondition(object a, object b) { ClassName=b; }
    }
}
'@
$expectedPid=201;$handleValue=101
function IsVisible($element) { return $element.Current.Visible }
$profile=[PSCustomObject]@{}
$profile | Add-Member ScriptMethod GetRuntimeId { return 'profile-1395' }
$profile | Add-Member ScriptMethod FindAll {
    param($scope,$condition)
    if ($condition.ClassName -ne 'class Ui::MarqueeLabel') { throw 'An unrelated field was inspected' }
    $script:queries++
    if ($script:mode -eq 'query_error') { throw 'Header provider unavailable' }
    $info=[PSCustomObject]@{ProcessId=201;Visible=$true;ControlType=[System.Windows.Automation.ControlType]::Text;
        ClassName='class Ui::MarqueeLabel';Name='1395';IsEnabled=$true}
    if ($script:mode -eq 'old') { $info.Name='Mouraa' }
    if ($script:mode -eq 'hidden') { $info.Visible=$false }
    if ($script:mode -eq 'other_pid') { $info.ProcessId=202 }
    if ($script:mode -eq 'wrong_type') { $info.ControlType=[System.Windows.Automation.ControlType]::Group }
    if ($script:mode -eq 'title_error') {
        $info.PSObject.Properties.Remove('Name')
        $info | Add-Member ScriptProperty Name { throw 'Numeric title name unavailable' }
    }
    $node=[PSCustomObject]@{Current=$info}
    # Any attempt to walk a subtree or read patterns would fail.
    $node | Add-Member ScriptMethod GetSupportedPatterns { throw 'Unrelated pattern query' }
    if ($script:mode -eq 'duplicate') { return @($node,$node) }
    return @($node)
}
foreach ($mode in @('ready','old','query_error','title_error','hidden','other_pid','wrong_type','duplicate')) {
    $script:mode=$mode;$script:queries=0
    $r=ReadProfileNumberReport $profile
    if (-not $r.ok -or -not $r.read_only -or -not $r.profile_number_only -or $r.scope_runtime_id -ne 'profile-1395' -or
            $r.window_handle -ne 101 -or $r.process_id -ne 201 -or $r.truncated -or $script:queries -ne 1) { throw 'Missing bound read-only report' }
    $names=@($r.controls | Where-Object { $_.type -eq 'Text' })
    switch ($mode) {
        'ready' { if ($names.Count -ne 1 -or $names[0].name -ne '1395' -or $r.errors.Count) { throw 'Ready title not read' } }
        'old' { if ($names.Count -ne 1 -or $names[0].name -ne 'Mouraa' -or $r.errors.Count) { throw 'Stale title was changed' } }
        'query_error' { if ($r.errors.Count -ne 1 -or $names.Count) { throw 'Query failure was hidden' } }
        'title_error' { if ($r.errors.Count -ne 1 -or $names.Count) { throw 'Title read failure was hidden' } }
        'duplicate' { if ($names.Count -ne 2) { throw 'Duplicate titles were collapsed' } }
        default { if ($names.Count -or $r.errors.Count) { throw 'Invisible or unrelated control accepted' } }
    }
}
Write-Output '8 production numeric-header scenarios passed; unrelated fields and TreeWalker are never read.'
