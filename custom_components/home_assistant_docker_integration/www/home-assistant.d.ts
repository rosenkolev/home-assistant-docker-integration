//
// FROM: https://github.com/home-assistant/frontend/blob/master/src/types.ts
//

import type {
  HassConfig,
  HassEntities,
  HassServices,
  HassServiceTarget,
  MessageBase,
} from './home-assistant-js-websocket';

export interface ServiceCallRequest {
  domain: string;
  service: string;
  serviceData?: Record<string, any>;
  target?: HassServiceTarget;
}

export interface ServiceCallResponse<T = any> {
  context: Context;
  response?: T;
}

export interface HomeAssistant {
  connected: boolean;
  states: HassEntities;
  services: HassServices;
  config: HassConfig;
  callService<T = any>(
    domain: ServiceCallRequest["domain"],
    service: ServiceCallRequest["service"],
    serviceData?: ServiceCallRequest["serviceData"],
    target?: ServiceCallRequest["target"],
    notifyOnError?: boolean,
    returnResponse?: boolean
  ): Promise<ServiceCallResponse<T>>;
  callWS<T>(msg: MessageBase): Promise<T>;
}