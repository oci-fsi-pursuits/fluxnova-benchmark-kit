param(
    [Parameter(Mandatory=$true)][string]$ContentPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [Parameter(Mandatory=$true)][string]$PreviewDirectory
)
$ErrorActionPreference = 'Stop'
$content = Get-Content -LiteralPath $ContentPath -Encoding UTF8 -Raw | ConvertFrom-Json
$outFile = [IO.Path]::GetFullPath($OutputPath)
$preview = [IO.Path]::GetFullPath($PreviewDirectory)
if (Test-Path -LiteralPath $outFile) {throw 'Choose a new output filename to preserve the earlier deck.'}
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outFile)) | Out-Null
[IO.Directory]::CreateDirectory($preview) | Out-Null
function Color([string]$hex) {
    $hex=$hex.TrimStart('#')
    return [Convert]::ToInt32($hex.Substring(0,2),16) + 256*[Convert]::ToInt32($hex.Substring(2,2),16) + 65536*[Convert]::ToInt32($hex.Substring(4,2),16)
}
function Add-Text($slide,$block) {
    $shape=$slide.Shapes.AddTextbox(1,[single]$block.x,[single]$block.y,[single]$block.w,[single]$block.h)
    $shape.Name='Text-'+$slide.SlideIndex+'-'+$slide.Shapes.Count
    $shape.TextFrame.MarginLeft=0;$shape.TextFrame.MarginRight=0
    $shape.TextFrame.MarginTop=0;$shape.TextFrame.MarginBottom=0
    $shape.TextFrame.WordWrap=-1;$shape.TextFrame.AutoSize=0
    $range=$shape.TextFrame.TextRange;$range.Text=[string]$block.text
    $range.Font.Name='Arial';$range.Font.Size=[single]$block.size
    $range.Font.Color.RGB=Color $(if($block.color){$block.color}else{'263A38'})
    $range.Font.Bold=$(if($block.bold){-1}else{0})
    $range.ParagraphFormat.SpaceAfter=5
    if($block.url){$range.ActionSettings.Item(1).Hyperlink.Address=[string]$block.url}
    $shape.TextFrame2.AutoSize=0
    $shape.Left=[single]$block.x;$shape.Top=[single]$block.y
    $shape.Width=[single]$block.w;$shape.Height=[single]$block.h
    return $shape
}
function Add-Table($slide,$spec) {
    $rows=@($spec.rows);$cols=@($spec.headers)
    $heights=@($spec.rowHeights);$totalHeight=($heights | Measure-Object -Sum).Sum
    $width=($spec.widths | Measure-Object -Sum).Sum
    $shape=$slide.Shapes.AddTable($rows.Count+1,$cols.Count,[single]$spec.x,[single]$spec.y,[single]$width,[single]$totalHeight)
    $shape.Name='Evidence-table-'+$slide.SlideIndex
    $table=$shape.Table
    for($c=1;$c -le $cols.Count;$c++){$table.Columns.Item($c).Width=[single]$spec.widths[$c-1]}
    for($r=1;$r -le $rows.Count+1;$r++){
        for($c=1;$c -le $cols.Count;$c++){
            $cell=$table.Cell($r,$c);$sh=$cell.Shape
            $sh.TextFrame.MarginLeft=9;$sh.TextFrame.MarginRight=9
            $sh.TextFrame.MarginTop=6;$sh.TextFrame.MarginBottom=4
            # Native PowerPoint table cells wrap automatically.
            $tr=$sh.TextFrame.TextRange
            $tr.Text=$(if($r -eq 1){[string]$cols[$c-1]}else{[string]$rows[$r-2][$c-1]})
            $tr.Font.Name='Arial';$tr.Font.Size=[single]$spec.fontSize
            $tr.Font.Bold=$(if($r -eq 1){-1}else{0})
            $tr.Font.Color.RGB=Color $(if($r -eq 1){'FFFFFF'}else{'263A38'})
            $sh.Fill.ForeColor.RGB=Color $(if($r -eq 1){'075B51'}elseif($r%2 -eq 0){'FFFFFF'}else{'F0F3F1'})
            for($b=1;$b -le 4;$b++){$cell.Borders.Item($b).ForeColor.RGB=Color 'D9E1DD';$cell.Borders.Item($b).Weight=.5}
        }
        $table.Rows.Item($r).Height=[single]$heights[$r-1]
    }
    return $shape
}
$app=New-Object -ComObject PowerPoint.Application
$existing=$app.Presentations.Count;$deck=$null
try {
    $deck=$app.Presentations.Add(0)
    $deck.PageSetup.SlideWidth=960;$deck.PageSetup.SlideHeight=540
    $checks=New-Object System.Collections.Generic.List[object]
    foreach($item in $content.slides){
        $slide=$deck.Slides.Add($deck.Slides.Count+1,12)
        $slide.FollowMasterBackground=0;$slide.Background.Fill.ForeColor.RGB=Color 'F7F8F4'
        foreach($rect in $item.rects){
            $shape=$slide.Shapes.AddShape(1,[single]$rect.x,[single]$rect.y,[single]$rect.w,[single]$rect.h)
            $shape.Fill.ForeColor.RGB=Color $rect.color;$shape.Line.Visible=0
        }
        foreach($line in $item.lines){
            $shape=$slide.Shapes.AddLine([single]$line.x1,[single]$line.y1,[single]$line.x2,[single]$line.y2)
            $shape.Line.ForeColor.RGB=Color $(if($line.color){$line.color}else{'D9E1DD'})
            $shape.Line.Weight=[single]$(if($line.weight){$line.weight}else{1})
        }
        if($item.title){Add-Text $slide ([PSCustomObject]@{text=$item.title;x=48;y=33;w=864;h=73;size=32;bold=$true}) | Out-Null}
        foreach($block in $item.blocks){Add-Text $slide $block | Out-Null}
        foreach($table in $item.tables){Add-Table $slide $table | Out-Null}
        if($item.source){Add-Text $slide ([PSCustomObject]@{text=$item.source;x=48;y=508;w=864;h=18;size=11;color='576B65'}) | Out-Null}
        $notes=$slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange
        $notes.Text=[string]$item.notes;$notes.Font.Name='Arial';$notes.Font.Size=12
        foreach($shape in $slide.Shapes){
            if($shape.HasTable -eq -1){
                if($shape.Top+$shape.Height -gt 511){$checks.Add([PSCustomObject]@{slide=$slide.SlideIndex;type='table_bounds';bottom=$shape.Top+$shape.Height})}
                for($r=1;$r -le $shape.Table.Rows.Count;$r++){
                    for($c=1;$c -le $shape.Table.Columns.Count;$c++){
                        $sh=$shape.Table.Cell($r,$c).Shape;$need=$sh.TextFrame.TextRange.BoundHeight
                        if($need -gt $sh.Height-$sh.TextFrame.MarginTop-$sh.TextFrame.MarginBottom+2){$checks.Add([PSCustomObject]@{slide=$slide.SlideIndex;type='table_text_fit';row=$r;col=$c;needed=$need;available=$sh.Height})}
                    }
                }
                foreach($other in $slide.Shapes){
                    if($other.HasTable -ne -1 -and $other.HasTextFrame -eq -1 -and $other.TextFrame.TextRange.Text.Trim()){
                        $overlapX=[Math]::Min($shape.Left+$shape.Width,$other.Left+$other.Width)-[Math]::Max($shape.Left,$other.Left)
                        $overlapY=[Math]::Min($shape.Top+$shape.Height,$other.Top+$other.Height)-[Math]::Max($shape.Top,$other.Top)
                        if($overlapX -gt .5 -and $overlapY -gt .5){$checks.Add([PSCustomObject]@{slide=$slide.SlideIndex;type='table_text_overlap';name=$other.Name})}
                    }
                }
            } elseif($shape.HasTextFrame -eq -1) {
                if($shape.TextFrame.TextRange.BoundHeight -gt $shape.Height+2){$checks.Add([PSCustomObject]@{slide=$slide.SlideIndex;type='text_fit';name=$shape.Name;needed=$shape.TextFrame.TextRange.BoundHeight;available=$shape.Height})}
            }
        }
    }
    $draft=Join-Path $preview 'candidate.pptx';$deck.SaveAs($draft,24)
    $checks | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $preview 'layout-checks.json') -Encoding UTF8
    if($checks.Count -gt 0){throw "Layout checks require repair: $($checks.Count). See private preview directory."}
    for($i=1;$i -le $deck.Slides.Count;$i++){$deck.Slides.Item($i).Export((Join-Path $preview ('slide-{0:D2}.png' -f $i)),'PNG',1600,900)}
    $deck.SaveAs($outFile,24);$count=$deck.Slides.Count;$deck.Close();$deck=$null
    $review=$app.Presentations.Open($outFile,-1,0,0)
    try {
        if($review.Slides.Count -ne $count){throw 'Saved deck slide count differs.'}
        $nativeTables=0
        foreach($slide in $review.Slides){foreach($shape in $slide.Shapes){if($shape.HasTable -eq -1){$nativeTables++}}}
    } finally {$review.Close();[Runtime.InteropServices.Marshal]::ReleaseComObject($review)|Out-Null}
    [PSCustomObject]@{output=$outFile;slides=$count;editable_native_tables=$nativeTables;rendered_slides=$count;existing_presentations_before=$existing;existing_presentations_after=$app.Presentations.Count} | ConvertTo-Json
} catch {
    Write-Output $_.ScriptStackTrace
    throw
} finally {
    if($null -ne $deck){$deck.Close();[Runtime.InteropServices.Marshal]::ReleaseComObject($deck)|Out-Null}
    [Runtime.InteropServices.Marshal]::ReleaseComObject($app)|Out-Null
}
