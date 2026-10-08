function Get-BlenderSyncNativeArtifactTargets {
    return @(
        [pscustomobject]@{ Platform = 'win_amd64'; PythonAbi = 'cpython-311'; ModuleFile = 'blendersync_native.pyd' },
        [pscustomobject]@{ Platform = 'linux_x86_64'; PythonAbi = 'cpython-311'; ModuleFile = 'blendersync_native.so' },
        [pscustomobject]@{ Platform = 'macos_arm64'; PythonAbi = 'cpython-311'; ModuleFile = 'blendersync_native.so' },
        [pscustomobject]@{ Platform = 'macos_x86_64'; PythonAbi = 'cpython-311'; ModuleFile = 'blendersync_native.so' }
    )
}
