# Execute production numeric guard expressions without Windows UI/input.
$ErrorActionPreference='Stop'
$cases=@(
    @{file='fill_contact.ps1';message='Invalid numeric contact name.'},
    @{file='submit_contact.ps1';message='Invalid phone or numeric remark.'},
    @{file='username_contact.ps1';message='Invalid contact number.'},
    @{file='close_contact_profile.ps1';message='Missing saved contact identity.'},
    @{file='select_member.ps1';message='Invalid numeric name.'},
    @{file='unregistered_contact.ps1';message='Missing bound result identity.'},
    @{file='prepare_group_page.ps1';message='Invalid saved contact snapshot.'}
)
$checks=0
foreach ($case in $cases) {
    $source=Get-Content -LiteralPath (Join-Path $PSScriptRoot $case.file) -Raw -Encoding UTF8
    $tokens=$null;$errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw $errors[0].Message }
    $guards=@($ast.FindAll({param($node)
        $node -is [System.Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.Contains($case.message)
    },$true) | Sort-Object { $_.Extent.Text.Length })
    if ($guards.Count -lt 1) { throw ('Missing production guard: '+$case.file) }
    $expression=[scriptblock]::Create($guards[0].Clauses[0].Item1.Extent.Text)
    foreach ($value in @('0','1','1395','999999','-1','1000000','abc')) {
        $number=$value;$phone='+5516991234567';$mode='fill';$profileId='profile-id';$rootId='root-id';$dialogId='dialog-id'
        $payload=@{number=$value};$p=@{number=$value};$contact=@{number=$value;phone=$phone}
        $invalid=& $expression
        $expected=$value -in @('-1','1000000','abc')
        if ([bool]$invalid -ne $expected) { throw ('Incorrect numeric guard: '+$case.file+' '+$value) }
        $checks++
    }
}
Write-Output ($checks.ToString()+' production numeric guard cases passed; zero allowed and invalid numbers rejected.')
