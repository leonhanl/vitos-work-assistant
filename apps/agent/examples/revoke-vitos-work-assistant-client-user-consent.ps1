#Requires -Version 7.0

<#
.SYNOPSIS
撤销指定 Entra 客户端应用的所有“用户同意”委托权限授权。

.DESCRIPTION
本脚本默认处理名为 vitos-work-assistant-client 的 Service Principal，并执行以下操作：

1. 检查 Microsoft.Graph 模块；未安装时为当前 macOS 用户安装。
2. 使用交互式浏览器登录 Microsoft Graph。
3. 按 displayName 精确查找客户端 Service Principal，并要求结果必须唯一。
4. 查询该客户端的 OAuth2PermissionGrant，只选择 ConsentType = Principal 的授权。
5. 在删除前显示 Grant ID、用户 Principal ID、资源名称、资源 ID 和 Scope。
6. 只有在操作者明确输入 DELETE 后才逐项删除。
7. 删除后重新查询并显示剩余的用户同意授权数量。
8. 无论成功、取消还是失败，最后都尝试断开 Microsoft Graph 会话。

安全边界：
- 本脚本不会删除 App Registration。
- 本脚本不会删除 Enterprise Application / Service Principal。
- 本脚本不会删除 ConsentType = AllPrincipals 的租户级管理员同意授权。
- 如果 displayName 找不到，或匹配到多个 Service Principal，脚本立即停止。

.PARAMETER AppDisplayName
要处理的客户端 Service Principal 的精确 displayName。
默认值：vitos-work-assistant-client

.NOTES
请在浏览器登录窗口中使用具备相应 Entra 管理角色的管理员账号登录。
请求的 Microsoft Graph delegated scopes：
- Application.Read.All
- DelegatedPermissionGrant.ReadWrite.All

删除成功后，已有 access token 可能在其自身有效期内继续有效；本脚本撤销的是后续颁发
access token 所依赖的 delegated permission grant。
#>

[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$AppDisplayName = "vitos-work-assistant-client"
)

# 使用严格模式，尽早发现变量拼写错误、未初始化变量等问题。
Set-StrictMode -Version Latest

# 让大多数 PowerShell/Graph 错误变成可被 try/catch 捕获的终止错误。
$ErrorActionPreference = "Stop"

# 记录本脚本是否已经成功连接 Graph；finally 中据此决定是否断开。
$graphConnected = $false

function Write-Section {
    param(
        [Parameter(Mandatory)]
        [string]$Title
    )

    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor DarkGray
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor DarkGray
}

function Get-TargetUserConsentGrants {
    param(
        [Parameter(Mandatory)]
        [string]$ClientServicePrincipalId
    )

    # 先在服务端按 clientId 过滤。这里的 clientId 是客户端 Service Principal 的
    # Object ID，不是 Application (client) ID / AppId。
    #
    # 随后在本地再次严格筛选 ConsentType = Principal。这样只会获得具体用户自己
    # 同意产生的 grants；ConsentType = AllPrincipals 的管理员同意不会进入删除列表。
    $clientGrants = @(
        Get-MgOauth2PermissionGrant `
            -Filter "clientId eq '$ClientServicePrincipalId'" `
            -All
    )

    return @(
        $clientGrants | Where-Object { $_.ConsentType -eq "Principal" }
    )
}

try {
    Write-Section "1. 检查 / 安装 Microsoft.Graph 模块"

    # Install-Module 由 PowerShellGet 提供。先明确检查，以便在环境不完整时给出
    # 容易理解的错误，而不是在后面得到“找不到命令”。
    if (-not (Get-Command Install-Module -ErrorAction SilentlyContinue)) {
        throw "当前 pwsh 中找不到 Install-Module。请先安装或更新 PowerShellGet。"
    }

    $installedGraph = Get-Module -ListAvailable -Name Microsoft.Graph |
        Sort-Object Version -Descending |
        Select-Object -First 1

    if ($null -eq $installedGraph) {
        Write-Host "未检测到 Microsoft.Graph，正在为当前用户安装……" -ForegroundColor Yellow

        # Scope CurrentUser 不需要 sudo；Force/AllowClobber 可减少首次安装时的交互。
        Install-Module Microsoft.Graph `
            -Scope CurrentUser `
            -Repository PSGallery `
            -Force `
            -AllowClobber `
            -Confirm:$false

        $installedGraph = Get-Module -ListAvailable -Name Microsoft.Graph |
            Sort-Object Version -Descending |
            Select-Object -First 1

        if ($null -eq $installedGraph) {
            throw "Install-Module 已结束，但仍未检测到 Microsoft.Graph。"
        }

        Write-Host "Microsoft.Graph 安装完成，版本：$($installedGraph.Version)" -ForegroundColor Green
    }
    else {
        Write-Host "已安装 Microsoft.Graph，版本：$($installedGraph.Version)" -ForegroundColor Green
    }

    # OAuth2PermissionGrant cmdlet 位于 Identity.SignIns，而 Service Principal 查询位于
    # Applications；身份验证命令位于 Authentication。显式导入可以更早发现模块问题。
    $requiredModules = @(
        "Microsoft.Graph.Authentication",
        "Microsoft.Graph.Applications",
        "Microsoft.Graph.Identity.SignIns"
    )

    foreach ($moduleName in $requiredModules) {
        if (-not (Get-Module -ListAvailable -Name $moduleName)) {
            throw "缺少必要子模块：$moduleName。请重新安装 Microsoft.Graph。"
        }

        Import-Module $moduleName -ErrorAction Stop
    }

    Write-Section "2. 使用管理员账号登录 Microsoft Graph"

    # 清除当前 pwsh 进程里可能已有的 Graph 会话，避免无意中复用 Bob/Alice 等账号。
    Disconnect-MgGraph -ErrorAction SilentlyContinue | Out-Null

    Write-Host "浏览器打开后，请使用 Entra 管理员账号登录。" -ForegroundColor Yellow
    Write-Host "请求 scopes：Application.Read.All, DelegatedPermissionGrant.ReadWrite.All"

    Connect-MgGraph `
        -Scopes "Application.Read.All", "DelegatedPermissionGrant.ReadWrite.All" `
        -ContextScope Process `
        -NoWelcome

    $graphConnected = $true

    # 清晰显示当前账号和 tenant，便于操作者在任何删除动作前再次核对身份。
    $context = Get-MgContext
    if ($null -eq $context) {
        throw "Connect-MgGraph 返回后未能取得 Graph context。"
    }

    Write-Host "登录成功：" -ForegroundColor Green
    $context | Select-Object Account, TenantId, AuthType, ContextScope, Scopes | Format-List

    Write-Section "3. 精确查找客户端 Service Principal"

    # OData 字符串中的单引号必须写成两个单引号，避免 displayName 含单引号时
    # 破坏过滤表达式。
    $escapedDisplayName = $AppDisplayName.Replace("'", "''")
    $servicePrincipals = @(
        Get-MgServicePrincipal `
            -Filter "displayName eq '$escapedDisplayName'" `
            -All
    )

    # 安全保护：只有唯一匹配时才继续。0 个或多个匹配都不做任何删除。
    if ($servicePrincipals.Count -ne 1) {
        if ($servicePrincipals.Count -gt 0) {
            Write-Host "找到以下同名 Service Principal：" -ForegroundColor Yellow
            $servicePrincipals |
                Select-Object DisplayName, Id, AppId |
                Format-Table -AutoSize
        }

        throw "按 displayName '$AppDisplayName' 应当且只能匹配 1 个 Service Principal；实际匹配 $($servicePrincipals.Count) 个。已停止，未删除任何 grant。"
    }

    $clientSp = $servicePrincipals[0]
    Write-Host "已确认唯一目标：" -ForegroundColor Green
    $clientSp | Select-Object DisplayName, Id, AppId | Format-List

    Write-Section "4. 列出该客户端的所有用户同意 grants（ConsentType = Principal）"

    $grants = @(Get-TargetUserConsentGrants -ClientServicePrincipalId $clientSp.Id)

    if ($grants.Count -eq 0) {
        Write-Host "没有找到需要删除的用户同意 OAuth2PermissionGrant。" -ForegroundColor Green
        Write-Host "剩余数量：0"
        return
    }

    # ResourceId 指向“被调用 API”的 Service Principal。逐个解析 displayName，
    # 这样删除预览里既有易读的资源名称，也保留精确的 Object ID。
    $resourceNameCache = @{}
    $previewRows = foreach ($grant in $grants) {
        $resourceName = "<无法解析>"

        if (-not [string]::IsNullOrWhiteSpace($grant.ResourceId)) {
            if (-not $resourceNameCache.ContainsKey($grant.ResourceId)) {
                try {
                    $resourceSp = Get-MgServicePrincipal `
                        -ServicePrincipalId $grant.ResourceId `
                        -ErrorAction Stop
                    $resourceNameCache[$grant.ResourceId] = $resourceSp.DisplayName
                }
                catch {
                    # 资源名称解析失败不应隐藏 grant。保留 ID 并明确标记名称无法解析。
                    $resourceNameCache[$grant.ResourceId] = "<无法解析：$($_.Exception.Message)>"
                }
            }

            $resourceName = $resourceNameCache[$grant.ResourceId]
        }

        [PSCustomObject]@{
            GrantId                   = $grant.Id
            UserPrincipalObjectId     = $grant.PrincipalId
            ResourceName              = $resourceName
            ResourceServicePrincipalId = $grant.ResourceId
            Scope                     = $grant.Scope
        }
    }

    Write-Host "以下是即将删除的完整目标列表，共 $($grants.Count) 项：" -ForegroundColor Yellow
    $previewRows | Format-Table -AutoSize -Wrap

    Write-Host ""
    Write-Host "再次核对客户端：" -ForegroundColor Yellow
    Write-Host "  DisplayName：$($clientSp.DisplayName)"
    Write-Host "  Service Principal Object ID：$($clientSp.Id)"
    Write-Host "  Application (client) ID：$($clientSp.AppId)"
    Write-Host "  待删除 grant 数量：$($grants.Count)"
    Write-Host "  删除范围：仅 ConsentType = Principal；不包含 AllPrincipals" -ForegroundColor Yellow

    Write-Section "5. 明确确认后删除"

    # 使用不容易误触的确认词，而不是简单的 y/n。只有精确输入大写 DELETE 才执行。
    $confirmation = Read-Host "若确认删除上面列出的全部 grants，请输入大写 DELETE；其他输入均取消"

    if ($confirmation -cne "DELETE") {
        Write-Host "操作已取消，未删除任何 grant。" -ForegroundColor Yellow
        return
    }

    $deletedCount = 0
    $failedDeletes = [System.Collections.Generic.List[object]]::new()

    foreach ($grant in $grants) {
        # 每次删除前打印这一个目标，方便审计，也能在中途失败时看出处理进度。
        $row = $previewRows | Where-Object { $_.GrantId -eq $grant.Id } | Select-Object -First 1
        Write-Host ""
        Write-Host "正在删除：" -ForegroundColor Yellow
        $row | Format-List

        try {
            Remove-MgOauth2PermissionGrant `
                -OAuth2PermissionGrantId $grant.Id `
                -Confirm:$false `
                -ErrorAction Stop

            $deletedCount++
            Write-Host "已删除 Grant ID：$($grant.Id)" -ForegroundColor Green
        }
        catch {
            # 尝试继续删除其他明确列出的目标，并在最后统一报告失败项。
            $failedDeletes.Add(
                [PSCustomObject]@{
                    GrantId = $grant.Id
                    Error   = $_.Exception.Message
                }
            )
            Write-Warning "删除失败，Grant ID：$($grant.Id)；错误：$($_.Exception.Message)"
        }
    }

    Write-Section "6. 删除后验证"

    # Graph 目录数据可能存在短暂复制延迟。最多查询 3 次；如果第一次已经是 0，
    # 就不再等待。这里的验证条件仍然只针对该客户端的 Principal grants。
    $remainingGrants = @()
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $remainingGrants = @(
            Get-TargetUserConsentGrants -ClientServicePrincipalId $clientSp.Id
        )

        Write-Host "验证查询 $attempt/3：剩余数量 = $($remainingGrants.Count)"

        if ($remainingGrants.Count -eq 0) {
            break
        }

        if ($attempt -lt 3) {
            Start-Sleep -Seconds 2
        }
    }

    Write-Host "成功发出删除：$deletedCount 项"
    Write-Host "删除调用失败：$($failedDeletes.Count) 项"
    Write-Host "最终验证剩余：$($remainingGrants.Count) 项"

    if ($remainingGrants.Count -gt 0) {
        Write-Warning "仍查询到用户同意 grants。可能是删除失败或 Graph 复制延迟；请稍后重新运行脚本确认。"
        $remainingGrants |
            Select-Object Id, PrincipalId, ResourceId, Scope, ConsentType |
            Format-Table -AutoSize -Wrap
    }

    if ($failedDeletes.Count -gt 0) {
        Write-Host "失败详情：" -ForegroundColor Red
        $failedDeletes | Format-Table -AutoSize -Wrap
        throw "$($failedDeletes.Count) 个 grant 删除失败。"
    }

    if ($remainingGrants.Count -eq 0) {
        Write-Host "验证完成：该客户端已无 ConsentType = Principal 的用户同意 grants。" -ForegroundColor Green
    }
}
catch {
    # 统一错误出口。finally 仍会继续执行，以确保 Graph 会话被断开。
    Write-Host "" 
    Write-Host "脚本执行失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Write-Section "7. 断开 Microsoft Graph"

    if ($graphConnected) {
        try {
            Disconnect-MgGraph -ErrorAction Stop | Out-Null
            Write-Host "已断开 Microsoft Graph。" -ForegroundColor Green
        }
        catch {
            Write-Warning "断开 Microsoft Graph 时出现错误：$($_.Exception.Message)"
        }
    }
    else {
        Write-Host "本次脚本未建立 Graph 会话，无需断开。"
    }
}
