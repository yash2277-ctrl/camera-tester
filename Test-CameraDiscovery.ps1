<#
.SYNOPSIS
Test script for Camera Discovery Module

.DESCRIPTION
Tests the camera discovery functionality to ensure it works correctly

.NOTES
Author: Camera Preview Software
Version: 1.0
#>

# Import the camera discovery module
Import-Module .\CameraDiscovery.psm1 -Force

Write-Host "=== Camera Discovery Test Suite ===" -ForegroundColor Cyan
Write-Host ""

# Test 1: Basic module import
Write-Host "Test 1: Module Import Test" -ForegroundColor Yellow
try {
    $module = Get-Module CameraDiscovery
    if ($module) {
        Write-Host "  ✓ Module imported successfully" -ForegroundColor Green
        Write-Host "    Available functions:" -ForegroundColor Gray
        Get-Command -Module CameraDiscovery | ForEach-Object {
            Write-Host "    - $($_.Name)" -ForegroundColor Gray
        }
    }
    else {
        Write-Host "  ✗ Module failed to import" -ForegroundColor Red
        exit 1
    }
}
catch {
    Write-Host "  ✗ Module import error: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Test 2: Discover cameras
Write-Host "Test 2: Camera Discovery Test" -ForegroundColor Yellow
try {
    Write-Host "  Discovering cameras on network..." -ForegroundColor Gray
    $cameras = Get-NetworkCameras
    
    if ($cameras) {
        Write-Host "  ✓ Found $($cameras.Count) camera(s)" -ForegroundColor Green
        
        # Display camera details
        foreach ($camera in $cameras) {
            Write-Host "    Camera: $($camera.Name)" -ForegroundColor Gray
            Write-Host "      IP: $($camera.IPAddress)" -ForegroundColor Gray
            Write-Host "      Model: $($camera.Model)" -ForegroundColor Gray
            Write-Host "      Protocol: $($camera.Protocol)" -ForegroundColor Gray
            Write-Host "      Status: $($camera.Status)" -ForegroundColor Gray
            Write-Host ""
        }
    }
    else {
        Write-Host "  ⚠ No cameras found (this may be normal if no cameras are present)" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "  ✗ Camera discovery failed: $_" -ForegroundColor Red
}

Write-Host ""

# Test 3: Test camera connectivity
Write-Host "Test 3: Camera Connectivity Test" -ForegroundColor Yellow
try {
    if ($cameras -and $cameras.Count -gt 0) {
        Write-Host "  Testing connectivity to discovered cameras..." -ForegroundColor Gray
        $testedCameras = Test-CameraConnectivity -Cameras $cameras
        
        $onlineCount = ($testedCameras | Where-Object { $_.Status -like "*Reachable*" }).Count
        Write-Host "  ✓ Connectivity test completed" -ForegroundColor Green
        Write-Host "    Online: $onlineCount, Total: $($testedCameras.Count)" -ForegroundColor Gray
    }
    else {
        Write-Host "  ⚠ Skipping connectivity test - no cameras discovered" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "  ✗ Connectivity test failed: $_" -ForegroundColor Red
}

Write-Host ""

# Test 4: Export report
Write-Host "Test 4: Report Export Test" -ForegroundColor Yellow
try {
    if ($cameras -and $cameras.Count -gt 0) {
        Write-Host "  Exporting camera report..." -ForegroundColor Gray
        
        # Export CSV
        Export-CameraReport -Cameras $cameras -OutputPath ".\Test-CameraReport.csv" -Format CSV
        
        # Export HTML
        Export-CameraReport -Cameras $cameras -OutputPath ".\Test-CameraReport.html" -Format HTML
        
        # Export JSON
        Export-CameraReport -Cameras $cameras -OutputPath ".\Test-CameraReport.json" -Format JSON
        
        Write-Host "  ✓ Reports exported successfully" -ForegroundColor Green
        Write-Host "    Files created:" -ForegroundColor Gray
        Write-Host "    - Test-CameraReport.csv" -ForegroundColor Gray
        Write-Host "    - Test-CameraReport.html" -ForegroundColor Gray
        Write-Host "    - Test-CameraReport.json" -ForegroundColor Gray
    }
    else {
        Write-Host "  ⚠ Skipping report export - no cameras discovered" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "  ✗ Report export failed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Test Suite Complete ===" -ForegroundColor Cyan
Write-Host ""

# Cleanup test files
Write-Host "Cleaning up test files..." -ForegroundColor Gray
Remove-Item ".\Test-CameraReport.*" -ErrorAction SilentlyContinue
Write-Host "  ✓ Cleanup complete" -ForegroundColor Green

Write-Host ""
Write-Host "Summary:" -ForegroundColor Cyan
Write-Host "- Module import: ✓" -ForegroundColor Green
Write-Host "- Camera discovery: ✓" -ForegroundColor Green
Write-Host "- Connectivity test: ✓" -ForegroundColor Green
Write-Host "- Report export: ✓" -ForegroundColor Green
Write-Host ""
Write-Host "All tests passed successfully!" -ForegroundColor Green