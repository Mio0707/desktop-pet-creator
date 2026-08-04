$ErrorActionPreference = "Stop"

try {
  Add-Type -AssemblyName PresentationFramework
  Add-Type -AssemblyName PresentationCore
  Add-Type -AssemblyName WindowsBase

  $root = $PSScriptRoot
  $configPath = Join-Path $root "config.json"

  function Show-ErrorAndExit($message) {
    try { Set-Content -Path (Join-Path $PSScriptRoot "pet-error.log") -Value $message -Encoding UTF8 } catch {}
    try { [System.Windows.MessageBox]::Show($message, "桌面宠物启动失败") | Out-Null } catch {}
    exit 1
  }

  if (!(Test-Path $configPath)) {
    Show-ErrorAndExit "找不到 config.json。请确认 start-pet.bat、pet-desktop.ps1、config.json 在同一个文件夹。"
  }

  $config = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json

  function Get-ObjectPropertyValue($object, $name) {
    if ($null -eq $object) { return $null }
    foreach ($property in $object.PSObject.Properties) {
      if ($property.Name -eq $name) { return $property.Value }
    }
    return $null
  }

  function Get-ConfigNumber($name, $fallback) {
    $raw = Get-ObjectPropertyValue $config $name
    if ($null -ne $raw) {
      try {
        $value = [double]$raw
        if ($value -gt 0) { return $value }
      } catch {}
    }
    return $fallback
  }

  function Get-ConfigBool($name, $fallback) {
    $raw = Get-ObjectPropertyValue $config $name
    if ($null -ne $raw) {
      try { return [bool]$raw } catch {}
    }
    return $fallback
  }

  function Resolve-AssetPath($relativePath) {
    if ([string]::IsNullOrWhiteSpace($relativePath)) { return $null }
    $candidate = Join-Path $root ([string]$relativePath)
    if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    return $null
  }

  function New-BitmapImage($filePath) {
    $bitmap = New-Object System.Windows.Media.Imaging.BitmapImage
    $bitmap.BeginInit()
    $bitmap.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
    $bitmap.UriSource = New-Object System.Uri -ArgumentList @($filePath, [System.UriKind]::Absolute)
    $bitmap.EndInit()
    $bitmap.Freeze()
    return $bitmap
  }

  function Normalize-InteractionRows($rows) {
    if ($null -eq $rows) { return @() }
    if ($rows -is [System.Array]) { return @($rows) }
    return @($rows)
  }

  function Get-TriggerFallbackAction($triggerName) {
    switch ([string]$triggerName) {
      "hover" { return [string](Get-ObjectPropertyValue $config "hoverAction") }
      "click" { return [string](Get-ObjectPropertyValue $config "clickAction") }
      "doubleClick" { return [string](Get-ObjectPropertyValue $config "doubleClickAction") }
      "rest" { return [string](Get-ObjectPropertyValue $config "restAction") }
      default { return [string](Get-ObjectPropertyValue $config "defaultAction") }
    }
  }

  function Get-InteractionRowsByTrigger($triggerName) {
    $interactions = Get-ObjectPropertyValue $config "interactions"
    if ($null -eq $interactions) { return @() }

    # 不使用 $config.interactions.$triggerName 这种动态属性访问，避免 Windows PowerShell 5.1 兼容问题。
    $triggerRows = $null
    foreach ($property in $interactions.PSObject.Properties) {
      if ($property.Name -eq [string]$triggerName) {
        $triggerRows = $property.Value
        break
      }
    }
    return @(Normalize-InteractionRows $triggerRows)
  }

  function Get-RandomInteraction($triggerName) {
    $rows = @(Get-InteractionRowsByTrigger $triggerName)
    $validRows = @()
    foreach ($row in $rows) {
      if ($null -eq $row) { continue }
      $actionName = [string](Get-ObjectPropertyValue $row "action")
      $lineText = [string](Get-ObjectPropertyValue $row "text")
      if (![string]::IsNullOrWhiteSpace($actionName) -or ![string]::IsNullOrWhiteSpace($lineText)) {
        $validRows += ([PSCustomObject]@{ action = $actionName; text = $lineText })
      }
    }

    if ($validRows.Count -gt 0) {
      if ($validRows.Count -eq 1) { return $validRows[0] }
      $index = Get-Random -Minimum 0 -Maximum $validRows.Count
      return $validRows[$index]
    }

    return [PSCustomObject]@{ action = (Get-TriggerFallbackAction $triggerName); text = "" }
  }

  function Get-LongestBubbleTextLength() {
    $max = 0
    try {
      $baseText = [string](Get-ObjectPropertyValue $config "bubbleText")
      if ($baseText.Length -gt $max) { $max = $baseText.Length }
      foreach ($trigger in @("rest", "hover", "click", "doubleClick")) {
        foreach ($row in @(Get-InteractionRowsByTrigger $trigger)) {
          $t = [string](Get-ObjectPropertyValue $row "text")
          if ($t.Length -gt $max) { $max = $t.Length }
        }
      }
    } catch {}
    return $max
  }

  $petWidth = (Get-ConfigNumber "width" 120)
  $petHeight = (Get-ConfigNumber "height" 140)
  $restDelayMs = (Get-ConfigNumber "restDelayMs" 480000)                 # 休息状态下，每 8 分钟随机切换一次休息状态
  $idleAfterInteractionMs = (Get-ConfigNumber "idleAfterInteractionMs" 60000)  # 交互后保持 1 分钟待机
  $actionDurationMs = (Get-ConfigNumber "actionDurationMs" 1100)
  $bubbleDisplayMs = (Get-ConfigNumber "bubbleDisplayMs" 3000)
  $bubbleEnabled = (Get-ConfigBool "bubbleEnabled" $true)
  $defaultAction = [string](Get-ObjectPropertyValue $config "defaultAction")
  if ([string]::IsNullOrWhiteSpace($defaultAction)) { $defaultAction = "idle" }
  $defaultBubbleText = [string](Get-ObjectPropertyValue $config "bubbleText")

  $longestTextLength = Get-LongestBubbleTextLength
  $textBasedWidth = [Math]::Min(460, [Math]::Max(180, ($longestTextLength * 18) + 52))
  $configuredWindowWidth = (Get-ConfigNumber "windowWidth" 0)
  $windowWidth = [Math]::Max([Math]::Max($configuredWindowWidth, $petWidth + 120), $textBasedWidth + 20)
  if (!$bubbleEnabled) { $windowWidth = [Math]::Max($configuredWindowWidth, $petWidth + 40) }
  $configuredWindowHeight = (Get-ConfigNumber "windowHeight" 0)
  $windowHeight = [Math]::Max($configuredWindowHeight, $petHeight + $(if ($bubbleEnabled) { 88 } else { 28 }))

  $actions = @{}
  $configActions = Get-ObjectPropertyValue $config "actions"
  if ($null -ne $configActions) {
    foreach ($property in $configActions.PSObject.Properties) {
      $path = Resolve-AssetPath $property.Value
      if ($path) { $actions[$property.Name] = New-BitmapImage $path }
    }
  }

  if (!$actions.ContainsKey($defaultAction)) {
    Show-ErrorAndExit "默认状态图片不存在：$defaultAction。请检查 config.json 里的 defaultAction 和 assets 文件夹。"
  }

  # ---- 帧序列：frames[state] = { files:[相对路径...], fps:N }，用于一状态多图连播 ----
  $frames = @{}
  $frameFps = @{}
  $configFrames = Get-ObjectPropertyValue $config "frames"
  if ($null -ne $configFrames) {
    foreach ($fprop in $configFrames.PSObject.Properties) {
      $st = [string]$fprop.Name
      $fval = $fprop.Value
      $rawFiles = Get-ObjectPropertyValue $fval "files"
      $rawFps = Get-ObjectPropertyValue $fval "fps"
      $fileList = $null
      if ($null -ne $rawFiles) {
        if ($rawFiles -is [System.Array]) { $fileList = @($rawFiles) } else { $fileList = @($rawFiles) }
      }
      $fps = 8
      if ($null -ne $rawFps) { try { $f = [int]$rawFps; if ($f -gt 0) { $fps = $f } } catch {} }
      if ($null -ne $fileList -and $fileList.Count -gt 0) {
        $imgs = @()
        foreach ($fr in $fileList) {
          $p = Resolve-AssetPath ([string]$fr)
          if ($p) { $imgs += (New-BitmapImage $p) }
        }
        if ($imgs.Count -gt 0) {
          $frames[$st] = $imgs
          $frameFps[$st] = $fps
        }
      }
    }
  }

  $window = New-Object System.Windows.Window
  $window.Title = if ([string]::IsNullOrWhiteSpace($config.petName)) { "桌面宠物" } else { [string]$config.petName }
  $window.Width = $windowWidth
  $window.Height = $windowHeight
  $window.WindowStyle = [System.Windows.WindowStyle]::None
  $window.AllowsTransparency = $true
  $window.Background = [System.Windows.Media.Brushes]::Transparent
  $window.Topmost = (Get-ConfigBool "alwaysOnTop" $true)
  $window.ShowInTaskbar = $false
  $window.ResizeMode = [System.Windows.ResizeMode]::NoResize
  $window.Left = [System.Windows.SystemParameters]::WorkArea.Width - $windowWidth - 40
  $window.Top = [System.Windows.SystemParameters]::WorkArea.Height - $windowHeight - 40

  $canvas = New-Object System.Windows.Controls.Canvas
  $canvas.Width = $windowWidth
  $canvas.Height = $windowHeight
  $canvas.Background = [System.Windows.Media.Brushes]::Transparent
  $window.Content = $canvas

  $bubble = New-Object System.Windows.Controls.Border
  $bubble.CornerRadius = New-Object System.Windows.CornerRadius -ArgumentList 12
  $bubble.Padding = New-Object System.Windows.Thickness -ArgumentList 10,6,10,6
  $bubble.Background = New-Object System.Windows.Media.SolidColorBrush -ArgumentList ([System.Windows.Media.Color]::FromArgb(240,255,255,255))
  $bubble.BorderBrush = New-Object System.Windows.Media.SolidColorBrush -ArgumentList ([System.Windows.Media.Color]::FromRgb(220,220,220))
  $bubble.BorderThickness = New-Object System.Windows.Thickness -ArgumentList 1
  $bubble.MaxWidth = [Math]::Max(150, $windowWidth - 16)

  $bubbleText = New-Object System.Windows.Controls.TextBlock
  $bubbleText.Text = $defaultBubbleText
  $bubbleText.Foreground = [System.Windows.Media.Brushes]::Black
  $bubbleText.FontSize = 14
  $bubbleText.TextWrapping = [System.Windows.TextWrapping]::Wrap
  $bubbleText.MaxWidth = [Math]::Max(120, $bubble.MaxWidth - 24)
  $bubble.Child = $bubbleText

  if ($bubbleEnabled) {
    $canvas.Children.Add($bubble) | Out-Null
    $bubble.Visibility = [System.Windows.Visibility]::Collapsed
  }

  $image = New-Object System.Windows.Controls.Image
  $image.Width = $petWidth
  $image.Height = $petHeight
  $image.Stretch = [System.Windows.Media.Stretch]::Uniform
  $image.Source = $actions[$defaultAction]
  $image.Cursor = [System.Windows.Input.Cursors]::Hand

  # ===== 程序动画：以脚底为中心做缩放/旋转/平移变换 =====
  $scaleT = New-Object System.Windows.Media.ScaleTransform(1, 1)
  $rotateT = New-Object System.Windows.Media.RotateTransform(0)
  $translateT = New-Object System.Windows.Media.TranslateTransform(0, 0)
  $transformGroup = New-Object System.Windows.Media.TransformGroup
  $transformGroup.Children.Add($scaleT) | Out-Null
  $transformGroup.Children.Add($rotateT) | Out-Null
  $transformGroup.Children.Add($translateT) | Out-Null
  $image.RenderTransformOrigin = New-Object System.Windows.Point(0.5, 1.0)
  $image.RenderTransform = $transformGroup

  function Stop-PetAnimations() {
    try {
      $scaleT.BeginAnimation([System.Windows.Media.ScaleTransform]::ScaleXProperty, $null)
      $scaleT.BeginAnimation([System.Windows.Media.ScaleTransform]::ScaleYProperty, $null)
      $rotateT.BeginAnimation([System.Windows.Media.RotateTransform]::AngleProperty, $null)
      $translateT.BeginAnimation([System.Windows.Media.TranslateTransform]::YProperty, $null)
      $scaleT.ScaleX = 1; $scaleT.ScaleY = 1
      $rotateT.Angle = 0; $translateT.Y = 0
    } catch {}
  }

  function Start-Breathing() {
    # 待机/休息时的呼吸起伏：缓慢缩放，无限往复
    Stop-PetAnimations
    try {
      $ease = New-Object System.Windows.Media.Animation.SineEase
      $ease.EasingMode = [System.Windows.Media.Animation.EasingMode]::EaseInOut
      $dur = New-Object System.Windows.Duration([TimeSpan]::FromSeconds(1.8))
      $animX = New-Object System.Windows.Media.Animation.DoubleAnimation(1.0, 1.045, $dur)
      $animX.AutoReverse = $true
      $animX.RepeatBehavior = [System.Windows.Media.Animation.RepeatBehavior]::Forever
      $animX.EasingFunction = $ease
      $animY = New-Object System.Windows.Media.Animation.DoubleAnimation(1.0, 1.03, $dur)
      $animY.AutoReverse = $true
      $animY.RepeatBehavior = [System.Windows.Media.Animation.RepeatBehavior]::Forever
      $animY.EasingFunction = $ease
      $scaleT.BeginAnimation([System.Windows.Media.ScaleTransform]::ScaleXProperty, $animX)
      $scaleT.BeginAnimation([System.Windows.Media.ScaleTransform]::ScaleYProperty, $animY)
    } catch {}
  }

  function Play-KeyFrames($target, $property, $ms, $pairs) {
    # $pairs: @(@(百分比, 值), ...)，如 @(@(0,0), @(0.4,-20), @(1,0))
    try {
      $ka = New-Object System.Windows.Media.Animation.DoubleAnimationUsingKeyFrames
      $ka.Duration = New-Object System.Windows.Duration([TimeSpan]::FromMilliseconds($ms))
      foreach ($pair in $pairs) {
        $kf = New-Object System.Windows.Media.Animation.LinearDoubleKeyFrame(
          [double]$pair[1],
          [System.Windows.Media.Animation.KeyTime]::FromPercent([double]$pair[0]))
        $ka.KeyFrames.Add($kf) | Out-Null
      }
      $target.BeginAnimation($property, $ka)
    } catch {}
  }

  function Play-InteractionAnimation($triggerName) {
    Stop-PetAnimations
    switch ([string]$triggerName) {
      "hover" {
        # 歪头：0 → 7° → -5° → 0
        Play-KeyFrames $rotateT ([System.Windows.Media.RotateTransform]::AngleProperty) 900 @(@(0,0), @(0.35,7), @(0.7,-5), @(1,0))
      }
      "click" {
        # 弹跳：1 → 1.12 → 1
        Play-KeyFrames $scaleT ([System.Windows.Media.ScaleTransform]::ScaleXProperty) 500 @(@(0,1), @(0.35,1.12), @(1,1))
        Play-KeyFrames $scaleT ([System.Windows.Media.ScaleTransform]::ScaleYProperty) 500 @(@(0,1), @(0.35,1.12), @(1,1))
      }
      "doubleClick" {
        # 跳跃：上移 20px 回落 + 轻微缩放
        Play-KeyFrames $translateT ([System.Windows.Media.TranslateTransform]::YProperty) 600 @(@(0,0), @(0.35,-20), @(0.7,2), @(1,0))
        Play-KeyFrames $scaleT ([System.Windows.Media.ScaleTransform]::ScaleXProperty) 600 @(@(0,1), @(0.35,1.06), @(1,1))
        Play-KeyFrames $scaleT ([System.Windows.Media.ScaleTransform]::ScaleYProperty) 600 @(@(0,1), @(0.35,1.06), @(1,1))
      }
      default {
        Start-Breathing
      }
    }
  }

  $petLeft = [Math]::Max(0, ($windowWidth - $petWidth) / 2)
  $petTop = [Math]::Max(0, $windowHeight - $petHeight - 8)

  function Update-BubblePosition() {
    if (!$bubbleEnabled) { return }
    try {
      $bubbleText.MaxWidth = [Math]::Max(120, $bubble.MaxWidth - 24)
      $measureSize = New-Object System.Windows.Size -ArgumentList @([double]$bubble.MaxWidth, [double]::PositiveInfinity)
      $bubble.Measure($measureSize)
      $bubbleWidth = [double]$bubble.DesiredSize.Width
      $bubbleHeight = [double]$bubble.DesiredSize.Height
      if ($bubbleWidth -lt 44) { $bubbleWidth = 44 }
      if ($bubbleHeight -lt 30) { $bubbleHeight = 30 }
      if ($bubbleWidth -gt $bubble.MaxWidth) { $bubbleWidth = [double]$bubble.MaxWidth }

      $bubbleLeft = $petLeft + (($petWidth - $bubbleWidth) / 2)
      $maxLeft = $windowWidth - $bubbleWidth - 4
      if ($bubbleLeft -lt 4) { $bubbleLeft = 4 }
      if ($bubbleLeft -gt $maxLeft) { $bubbleLeft = $maxLeft }

      $bubbleTop = $petTop - $bubbleHeight - 6
      if ($bubbleTop -lt 4) { $bubbleTop = 4 }

      [System.Windows.Controls.Canvas]::SetLeft($bubble, $bubbleLeft)
      [System.Windows.Controls.Canvas]::SetTop($bubble, $bubbleTop)
    } catch {}
  }

  [System.Windows.Controls.Canvas]::SetLeft($image, $petLeft)
  [System.Windows.Controls.Canvas]::SetTop($image, $petTop)
  $canvas.Children.Add($image) | Out-Null
  Update-BubblePosition

  $actionTimer = New-Object System.Windows.Threading.DispatcherTimer
  $actionTimer.Interval = [TimeSpan]::FromMilliseconds($actionDurationMs)

  $idleTimer = New-Object System.Windows.Threading.DispatcherTimer
  $idleTimer.Interval = [TimeSpan]::FromMilliseconds($idleAfterInteractionMs)

  $restCycleTimer = New-Object System.Windows.Threading.DispatcherTimer
  $restCycleTimer.Interval = [TimeSpan]::FromMilliseconds($restDelayMs)

  $bubbleTimer = New-Object System.Windows.Threading.DispatcherTimer
  $bubbleTimer.Interval = [TimeSpan]::FromMilliseconds($bubbleDisplayMs)

  # ---- 帧序列播放：进入某状态若有多帧，则按 fps 循环切换图片 Source ----
  $frameTimer = New-Object System.Windows.Threading.DispatcherTimer
  $script:currentFrameState = $null
  $script:frameIndex = 0

  function Set-StateDisplay($stateName) {
    try { $frameTimer.Stop() } catch {}
    $script:currentFrameState = $null
    if (![string]::IsNullOrWhiteSpace($stateName) -and $frames.ContainsKey($stateName) -and $frames[$stateName].Count -gt 0) {
      $arr = $frames[$stateName]
      $script:currentFrameState = $stateName
      $script:frameIndex = 0
      try { $image.Source = $arr[0] } catch {}
      $fps = if ($frameFps.ContainsKey($stateName)) { $frameFps[$stateName] } else { 8 }
      $intervalMs = [Math]::Max(40, [int](1000 / $fps))
      $frameTimer.Interval = [TimeSpan]::FromMilliseconds($intervalMs)
      $frameTimer.Start()
    } else {
      if (![string]::IsNullOrWhiteSpace($stateName) -and $actions.ContainsKey($stateName)) {
        try { $image.Source = $actions[$stateName] } catch {}
      } else {
        try { $image.Source = $actions[$defaultAction] } catch {}
      }
    }
  }

  $frameTimer.Add_Tick({
    if ($null -eq $script:currentFrameState) { return }
    $arr = $frames[$script:currentFrameState]
    if ($null -eq $arr -or $arr.Count -eq 0) { return }
    $script:frameIndex = ($script:frameIndex + 1) % $arr.Count
    try { $image.Source = $arr[$script:frameIndex] } catch {}
  })


  $interactionLockedUntil = [DateTime]::MinValue
  $currentMode = "rest"
  $currentTrigger = "rest"
  $pendingSingleClick = $false
  $pendingMouseDown = $false
  $mouseDownPoint = $null
  $dragThreshold = 5

  function Is-InteractionLocked() {
    return ([DateTime]::Now -lt $script:interactionLockedUntil)
  }

  function Stop-StateTimers() {
    $actionTimer.Stop()
    $idleTimer.Stop()
    $restCycleTimer.Stop()
  }

  function Hide-Bubble() {
    if (!$bubbleEnabled) { return }
    try {
      $bubbleTimer.Stop()
      $bubble.Visibility = [System.Windows.Visibility]::Collapsed
    } catch {}
  }

  function Show-Bubble($lineText) {
    if (!$bubbleEnabled) { return }
    $textToShow = [string]$lineText
    if ([string]::IsNullOrWhiteSpace($textToShow)) {
      Hide-Bubble
      return
    }

    $bubbleText.Text = $textToShow
    $bubble.Visibility = [System.Windows.Visibility]::Visible
    Update-BubblePosition

    $bubbleTimer.Stop()
    $bubbleTimer.Interval = [TimeSpan]::FromMilliseconds($bubbleDisplayMs)
    $bubbleTimer.Start()
  }

  function Show-FallbackDefault() {
    Set-StateDisplay $defaultAction
  }

  function Set-ImageByAction($actionName) {
    if (![string]::IsNullOrWhiteSpace($actionName) -and $actions.ContainsKey($actionName)) {
      Set-StateDisplay $actionName
    } else {
      Set-StateDisplay $defaultAction
    }
  }

  function Start-IdleAfterInteraction() {
    Stop-StateTimers
    $script:currentMode = "idle"
    $script:currentTrigger = "idle"
    $script:interactionLockedUntil = [DateTime]::MinValue
    Set-ImageByAction $defaultAction
    Start-Breathing
    # 气泡不在这里强制隐藏。所有气泡统一由 3 秒计时器自动隐藏。
    $idleTimer.Interval = [TimeSpan]::FromMilliseconds($idleAfterInteractionMs)
    $idleTimer.Start()
  }

  function Start-RestMode($showBubble) {
    Stop-StateTimers
    $script:currentMode = "rest"
    $script:currentTrigger = "rest"
    $script:interactionLockedUntil = [DateTime]::MinValue

    $picked = Get-RandomInteraction "rest"
    $restActionName = [string](Get-ObjectPropertyValue $picked "action")
    $restText = [string](Get-ObjectPropertyValue $picked "text")
    if ([string]::IsNullOrWhiteSpace($restActionName)) {
      $restActionName = [string](Get-TriggerFallbackAction "rest")
    }
    if ([string]::IsNullOrWhiteSpace($restActionName)) {
      $restActionName = $defaultAction
    }

    Set-ImageByAction $restActionName
    Start-Breathing
    if ($showBubble) { Show-Bubble $restText } else { Hide-Bubble }

    $restCycleTimer.Interval = [TimeSpan]::FromMilliseconds($restDelayMs)
    $restCycleTimer.Start()
  }

  function Start-Interaction($triggerName, $durationMs, $lockMs, $force) {
    # hover 不能挡住点击/双击；点击/双击可以强制覆盖 hover。
    if ((-not $force) -and (Is-InteractionLocked)) { return }

    Stop-StateTimers
    $script:currentMode = "action"
    $script:currentTrigger = [string]$triggerName
    if ($lockMs -gt 0) {
      $script:interactionLockedUntil = [DateTime]::Now.AddMilliseconds($lockMs)
    } else {
      $script:interactionLockedUntil = [DateTime]::MinValue
    }

    $picked = Get-RandomInteraction $triggerName
    $actionName = [string](Get-ObjectPropertyValue $picked "action")
    $lineText = [string](Get-ObjectPropertyValue $picked "text")

    Set-ImageByAction $actionName
    Show-Bubble $lineText
    Play-InteractionAnimation $triggerName

    $actionTimer.Interval = [TimeSpan]::FromMilliseconds($durationMs)
    $actionTimer.Start()
  }

  $bubbleTimer.Add_Tick({
    Hide-Bubble
  })

  $actionTimer.Add_Tick({
    $actionTimer.Stop()
    Start-IdleAfterInteraction
  })

  $idleTimer.Add_Tick({
    $idleTimer.Stop()
    Start-RestMode $false
  })

  $restCycleTimer.Add_Tick({
    $restCycleTimer.Stop()
    Start-RestMode $true
  })

  $clickTimer = New-Object System.Windows.Threading.DispatcherTimer
  try {
    $clickDelay = [int][System.Windows.SystemParameters]::DoubleClickTime + 80
  } catch {
    $clickDelay = 430
  }
  $clickTimer.Interval = [TimeSpan]::FromMilliseconds($clickDelay)
  $clickTimer.Add_Tick({
    $clickTimer.Stop()
    if ($script:pendingSingleClick) {
      $script:pendingSingleClick = $false
      Start-Interaction "click" 1300 1450 $true
    }
  })

  $image.Add_MouseEnter({
    # hover 只作为轻交互：不会锁死点击/双击。
    Start-Interaction "hover" 1000 0 $false
  })

  $image.Add_MouseLeftButtonDown({
    param($sender, $event)

    if ($event.ClickCount -ge 2) {
      $script:pendingSingleClick = $false
      $script:pendingMouseDown = $false
      $clickTimer.Stop()
      try { $image.ReleaseMouseCapture() } catch {}
      Start-Interaction "doubleClick" 1500 1650 $true
      $event.Handled = $true
      return
    }

    # 单击先延迟确认；如果在双击时间内来了第二下，就触发双击而不是单击。
    $script:pendingSingleClick = $true
    $script:pendingMouseDown = $true
    try { $script:mouseDownPoint = $event.GetPosition($window) } catch { $script:mouseDownPoint = $null }
    try { $image.CaptureMouse() | Out-Null } catch {}
    $clickTimer.Stop()
    $clickTimer.Interval = [TimeSpan]::FromMilliseconds($clickDelay)
    $clickTimer.Start()
    $event.Handled = $true
  })

  $image.Add_MouseMove({
    param($sender, $event)
    if (-not (Get-ConfigBool "draggable" $true)) { return }
    if (-not $script:pendingMouseDown) { return }
    if ($event.LeftButton -ne [System.Windows.Input.MouseButtonState]::Pressed) { return }

    try {
      $currentPoint = $event.GetPosition($window)
      if ($null -ne $script:mouseDownPoint) {
        $dx = [Math]::Abs($currentPoint.X - $script:mouseDownPoint.X)
        $dy = [Math]::Abs($currentPoint.Y - $script:mouseDownPoint.Y)
        if (($dx -lt $dragThreshold) -and ($dy -lt $dragThreshold)) { return }
      }
    } catch {}

    # 开始拖拽后，取消单击/双击候选。拖拽也算交互，结束后进入 1 分钟待机。
    $script:pendingSingleClick = $false
    $script:pendingMouseDown = $false
    $clickTimer.Stop()
    try { $image.ReleaseMouseCapture() } catch {}
    Start-IdleAfterInteraction
    try { $window.DragMove() } catch {}
    $event.Handled = $true
  })

  $image.Add_MouseLeftButtonUp({
    $script:pendingMouseDown = $false
    try { $image.ReleaseMouseCapture() } catch {}
  })

  $canvas.Add_MouseRightButtonDown({ $window.Close() })

  $canvas.Add_MouseLeftButtonDown({
    param($sender, $event)
    # 空白区域只负责拖拽，不抢宠物主体的单击/双击。
    if ((Get-ConfigBool "draggable" $true)) {
      Start-IdleAfterInteraction
      try { $window.DragMove() } catch {}
    }
    $event.Handled = $true
  })

  $window.Add_KeyDown({
    param($sender, $event)
    if ($event.Key -eq [System.Windows.Input.Key]::Escape) { $window.Close() }
  })

  $window.Add_ContentRendered({
    Start-RestMode $false
  })

  $window.ShowDialog() | Out-Null
}
catch {
  $message = "启动失败：`r`n" + $_.Exception.Message + "`r`n`r`n位置：`r`n" + $_.ScriptStackTrace
  try { Set-Content -Path (Join-Path $PSScriptRoot "pet-error.log") -Value $message -Encoding UTF8 } catch {}
  try {
    Add-Type -AssemblyName PresentationFramework -ErrorAction SilentlyContinue
    [System.Windows.MessageBox]::Show($message, "桌面宠物启动失败") | Out-Null
  } catch {
    Write-Host $message
    pause
  }
}
