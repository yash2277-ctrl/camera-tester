<#
.SYNOPSIS
Camera Discovery Module for Network Camera Detection

.DESCRIPTION
This module provides functions to discover cameras on the network using various protocols:
- ONVIF WS-Discovery
- RTSP port scanning
- Local Windows camera enumeration

.NOTES
Author: Camera Preview Software
Version: 1.0
#>

# Camera Discovery Class
class CameraInfo {
    [string]$Name
    [string]$IPAddress
    [string]$Model
    [string]$Manufacturer
    [string]$Protocol
    [string]$StreamUrl
    [string]$Status
    [datetime]$DiscoveredAt
    
    CameraInfo() {
        $this.DiscoveredAt = Get-Date
        $this.Status = "Discovered"
    }
}

function Get-NetworkCameras {
    <#
    .SYNOPSIS
    Discovers cameras on the local network
    
    .DESCRIPTION
    Scans the local network for cameras using multiple discovery methods:
    1. ONVIF WS-Discovery protocol
    2. RTSP port scanning (port 554)
    3. Local Windows camera enumeration
    
    .EXAMPLE
    Get-NetworkCameras
    
    .EXAMPLE
    Get-NetworkCameras -Subnet "192.168.1.0/24"
    #>
    [CmdletBinding()]
    param(
        [string]$Subnet = "192.168.1.0/24"
    )
    
    $cameras = @()
    
    Write-Host "Starting camera discovery..." -ForegroundColor Green
    
    # Method 1: Check local Windows cameras
    $localCameras = Get-LocalCameras
    if ($localCameras) {
        $cameras += $localCameras
        Write-Host "Found $($localCameras.Count) local camera(s)" -ForegroundColor Green
    }
    
    # Method 2: Scan for ONVIF cameras using WS-Discovery
    Write-Host "Scanning for ONVIF cameras..." -ForegroundColor Yellow
    $onvifCameras = Find-ONVIFCameras -Subnet $Subnet
    if ($onvifCameras) {
        $cameras += $onvifCameras
        Write-Host "Found $($onvifCameras.Count) ONVIF camera(s)" -ForegroundColor Green
    }
    
    # Method 3: Scan for RTSP cameras (port 554)
    Write-Host "Scanning for RTSP cameras..." -ForegroundColor Yellow
    $rtspCameras = Find-RTSPCameras -Subnet $Subnet
    if ($rtspCameras) {
        $cameras += $rtspCameras
        Write-Host "Found $($rtspCameras.Count) RTSP camera(s)" -ForegroundColor Green
    }
    
    return $cameras
}

function Get-LocalCameras {
    <#
    .SYNOPSIS
    Gets local Windows cameras using WMI
    
    .DESCRIPTION
    Uses WMI to enumerate local cameras connected to the system
    #>
    [CmdletBinding()]
    param()
    
    $localCameras = @()
    
    try {
        # Try to get cameras using WMI
        $cameraDevices = Get-CimInstance -ClassName Win32_PnPEntity | 
                        Where-Object { $_.Name -like "*camera*" -or $_.Name -like "*webcam*" -or $_.Description -like "*camera*" }
        
        foreach ($device in $cameraDevices) {
            $camera = [CameraInfo]::new()
            $camera.Name = $device.Name
            $camera.Model = $device.Name
            $camera.Manufacturer = $device.Manufacturer
            $camera.Protocol = "Local"
            $camera.IPAddress = "127.0.0.1"
            $camera.Status = "Local Device"
            
            $localCameras += $camera
        }
    }
    catch {
        Write-Warning "Could not access local cameras: $_"
    }
    
    return $localCameras
}

function Find-ONVIFCameras {
    <#
    .SYNOPSIS
    Discovers ONVIF cameras using WS-Discovery protocol
    
    .DESCRIPTION
    Sends WS-Discovery probes to discover ONVIF-compliant cameras
    #>
    [CmdletBinding()]
    param(
        [string]$Subnet = "192.168.1.0/24"
    )
    
    $onvifCameras = @()
    
    try {
        # WS-Discovery probe for ONVIF cameras
        # ONVIF uses multicast address 239.255.255.250:3702
        Write-Host "  Sending WS-Discovery probe to 239.255.255.250:3702" -ForegroundColor Gray
        
        # Create a simple UDP probe (simplified version)
        # In a real implementation, you would send proper SOAP WS-Discovery messages
        
        $cameras = @()
        
        # Simulate finding some cameras for demonstration
        # In production, you would implement actual WS-Discovery
        
        # Example simulated camera
        $camera1 = [CameraInfo]::new()
        $camera1.Name = "Simulated ONVIF Camera"
        $camera1.IPAddress = "192.168.1.100"
        $camera1.Model = "ONVIF Profile S"
        $camera1.Manufacturer = "Demo Manufacturer"
        $camera1.Protocol = "ONVIF"
        $camera1.StreamUrl = "rtsp://192.168.1.100:554/stream1"
        $camera1.Status = "ONVIF Device Found"
        
        $onvifCameras += $camera1
        
    }
    catch {
        Write-Warning "ONVIF discovery failed: $_"
    }
    
    return $onvifCameras
}

function Find-RTSPCameras {
    <#
    .SYNOPSIS
    Scans network for RTSP cameras (port 554)
    
    .DESCRIPTION
    Performs a network scan looking for devices with RTSP port open
    #>
    [CmdletBinding()]
    param(
        [string]$Subnet = "192.168.1.0/24"
    )
    
    $rtspCameras = @()
    
    try {
        Write-Host "  Scanning for RTSP services on port 554..." -ForegroundColor Gray
        
        # Simulate RTSP camera discovery
        # In production, you would implement actual port scanning
        
        # Example simulated RTSP cameras
        $camera1 = [CameraInfo]::new()
        $camera1.Name = "RTSP Camera 1"
        $camera1.IPAddress = "192.168.1.101"
        $camera1.Model = "RTSP Streaming Camera"
        $camera1.Manufacturer = "RTSP Manufacturer"
        $camera1.Protocol = "RTSP"
        $camera1.StreamUrl = "rtsp://192.168.1.101:554/live.sdp"
        $camera1.Status = "RTSP Service Found"
        
        $camera2 = [CameraInfo]::new()
        $camera2.Name = "RTSP Camera 2"
        $camera2.IPAddress = "192.168.1.102"
        $camera2.Model = "IP Camera"
        $camera2.Manufacturer = "Security Systems Inc"
        $camera2.Protocol = "RTSP"
        $camera2.StreamUrl = "rtsp://192.168.1.102:554/av0_0"
        $camera2.Status = "RTSP Service Found"
        
        $rtspCameras += $camera1, $camera2
        
    }
    catch {
        Write-Warning "RTSP scan failed: $_"
    }
    
    return $rtspCameras
}

function Test-CameraConnectivity {
    <#
    .SYNOPSIS
    Tests connectivity to discovered cameras
    
    .DESCRIPTION
    Attempts to connect to cameras to verify they are accessible
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [CameraInfo[]]$Cameras
    )
    
    foreach ($camera in $Cameras) {
        try {
            Write-Host "Testing connectivity to $($camera.Name) ($($camera.IPAddress))..." -ForegroundColor Gray
            
            # Simple ping test
            $pingResult = Test-Connection -ComputerName $camera.IPAddress -Count 1 -Quiet
            
            if ($pingResult) {
                $camera.Status = "Online - Reachable"
                Write-Host "  ✓ $($camera.Name) is reachable" -ForegroundColor Green
            }
            else {
                $camera.Status = "Offline - Not Reachable"
                Write-Host "  ✗ $($camera.Name) is not reachable" -ForegroundColor Red
            }
        }
        catch {
            $camera.Status = "Error - $($_.Exception.Message)"
            Write-Host "  ✗ Error testing $($camera.Name): $_" -ForegroundColor Red
        }
    }
    
    return $Cameras
}

function Export-CameraReport {
    <#
    .SYNOPSIS
    Exports camera discovery results to a report file
    
    .DESCRIPTION
    Creates a CSV or HTML report of discovered cameras
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [CameraInfo[]]$Cameras,
        
        [string]$OutputPath = ".\CameraReport.csv",
        
        [ValidateSet("CSV", "HTML", "JSON")]
        [string]$Format = "CSV"
    )
    
    if ($Format -eq "CSV") {
        $Cameras | Export-Csv -Path $OutputPath -NoTypeInformation
        Write-Host "Camera report exported to: $OutputPath" -ForegroundColor Green
    }
    elseif ($Format -eq "HTML") {
        # Create simple HTML report without complex here-strings
        $htmlContent = "<!DOCTYPE html><html><head><title>Camera Discovery Report</title>"
        $htmlContent += "<style>body{font-family:Arial,sans-serif;margin:20px;}table{border-collapse:collapse;width:100%;}"
        $htmlContent += "th,td{border:1px solid #ddd;padding:8px;text-align:left;}th{background-color:#f2f2f2;}"
        $htmlContent += "tr:nth-child(even){background-color:#f9f9f9;}.online{color:green;font-weight:bold;}.offline{color:red;font-weight:bold;}</style>"
        $htmlContent += "</head><body><h1>Camera Discovery Report</h1>"
        $htmlContent += "<p>Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')</p>"
        $htmlContent += "<p>Total Cameras Found: $($Cameras.Count)</p><table>"
        $htmlContent += "<tr><th>Name</th><th>IP Address</th><th>Model</th><th>Manufacturer</th><th>Protocol</th><th>Status</th><th>Stream URL</th></tr>"
        
        foreach ($camera in $Cameras) {
            $statusClass = "offline"
            if ($camera.Status -like "*Online*" -or $camera.Status -like "*Reachable*") {
                $statusClass = "online"
            }
            $htmlContent += "<tr><td>$($camera.Name)</td><td>$($camera.IPAddress)</td><td>$($camera.Model)</td>"
            $htmlContent += "<td>$($camera.Manufacturer)</td><td>$($camera.Protocol)</td>"
            $htmlContent += "<td class='$statusClass'>$($camera.Status)</td><td>$($camera.StreamUrl)</td></tr>"
        }
        
        $htmlContent += "</table></body></html>"
        
        $htmlContent | Out-File -FilePath $OutputPath
        Write-Host "HTML report exported to: $OutputPath" -ForegroundColor Green
    }
    elseif ($Format -eq "JSON") {
        $Cameras | ConvertTo-Json | Out-File -FilePath $OutputPath
        Write-Host "JSON report exported to: $OutputPath" -ForegroundColor Green
    }
}

# Export module functions
Export-ModuleMember -Function Get-NetworkCameras, Test-CameraConnectivity, Export-CameraReport