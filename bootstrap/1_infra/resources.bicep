// POC resources (resource group scope): NSG (22/80/443/9443 open) + a Linux VM — RHEL 9 PAYG (AAP,
// default) or Ubuntu LTS (AWX), selected by osFamily. Both images are PAYG (no marketplace plan).
// Application-level security is delegated to a containerized reverse proxy (TLS + auth).
// Base tooling installed at boot via cloud-init (cloud-init.rhel.yaml / cloud-init.ubuntu.yaml).
@description('Azure region')
param location string

@description('VM name (used as a prefix for network resources)')
param vmName string

@description('VM size')
param vmSize string

@description('Linux admin user')
param adminUsername string

@description('Public SSH key (contents of ~/.ssh/snow-aap-poc.pub)')
param sshPublicKey string

@description('DNS label -> <label>.<region>.cloudapp.azure.com')
param dnsLabel string

@description('OS disk size (GB)')
param osDiskSizeGb int = 128

@description('OS disk SKU')
param osDiskSku string = 'Premium_LRS'

@description('OS family: rhel or ubuntu')
@allowed([ 'rhel', 'ubuntu' ])
param osFamily string = 'rhel'

// Marketplace images per OS family (both PAYG — no plan / marketplace agreement needed).
var images = {
  rhel: {
    publisher: 'RedHat'
    offer: 'RHEL'
    sku: '9-lvm-gen2'
    version: 'latest'
  }
  ubuntu: {
    publisher: 'Canonical'
    offer: 'ubuntu-24_04-lts'
    sku: 'server'          // 24.04 LTS, Hyper-V Gen2 (the gen2 sku is 'server', not 'server-gen2')
    version: 'latest'
  }
}

var vnetName = '${vmName}-vnet'
var nsgName = '${vmName}-nsg'
var pipName = '${vmName}-pip'
var nicName = '${vmName}-nic'

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'allow-ssh'
        properties: {
          priority: 1001
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'allow-http'
        properties: {
          priority: 1002
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
      {
        name: 'allow-https'
        properties: {
          priority: 1003
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        // Meridian simulator edge (Caddy, TLS). :443 and :8443 are taken by AAP (gateway envoy +
        // hub/eda nginx), so the edge — and Keycloak behind it at /auth — live on :9443.
        name: 'allow-meridian-edge'
        properties: {
          priority: 1004
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '9443'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.42.0.0/16'] }
    subnets: [
      {
        name: 'default'
        properties: {
          addressPrefix: '10.42.1.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: pipName
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: pip.id }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      customData: base64(osFamily == 'ubuntu' ? loadTextContent('cloud-init.ubuntu.yaml') : loadTextContent('cloud-init.rhel.yaml'))
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: images[osFamily]
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: osDiskSizeGb
        managedDisk: { storageAccountType: osDiskSku }
      }
    }
    networkProfile: {
      networkInterfaces: [{ id: nic.id }]
    }
  }
}

output fqdn string = pip.properties.dnsSettings.fqdn
output publicIp string = pip.properties.ipAddress
