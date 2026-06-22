// Bicep entry point (subscription scope): creates the resource group, then deploys the
// resources via the resources.bicep module. Deployment: see README.md.
targetScope = 'subscription'

@description('Azure region')
param location string = 'westeurope'

@description('Resource group name')
param resourceGroupName string = 'rg-snow-aap-poc'

@description('VM name')
param vmName string = 'aap-poc'

@description('VM size')
param vmSize string = 'Standard_D4s_v5'

@description('Linux admin user')
param adminUsername string = 'azureuser'

@description('Public SSH key (contents of ~/.ssh/snow-aap-poc.pub)')
param sshPublicKey string

@description('DNS label (globally unique) -> <label>.<region>.cloudapp.azure.com')
param dnsLabel string = 'aap-poc'

@description('OS disk size (GB)')
param osDiskSizeGb int = 128

@description('OS disk SKU')
param osDiskSku string = 'Premium_LRS'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module resources 'resources.bicep' = {
  name: 'aap-poc-resources'
  scope: rg
  params: {
    location: location
    vmName: vmName
    vmSize: vmSize
    adminUsername: adminUsername
    sshPublicKey: sshPublicKey
    dnsLabel: dnsLabel
    osDiskSizeGb: osDiskSizeGb
    osDiskSku: osDiskSku
  }
}

output fqdn string = resources.outputs.fqdn
output publicIp string = resources.outputs.publicIp
output sshCommand string = 'ssh ${adminUsername}@${resources.outputs.fqdn}'
