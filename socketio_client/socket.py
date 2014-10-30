from socketio import has_bin
from socketio.event_emitter import EventEmitter
from engine.socket import Socket as EngineSocket
import socketio.parser as Parser

import logging
logger = logging.getLogger(__name__)

internal_events = {'connect', 'connect_error', 'connect_timeout', 'disconnect', 'error', 'reconnect',
                   'reconnect_attempt', 'reconnect_failed', 'reconnect_error', 'reconnecting'}


class Socket(EventEmitter):

    def __init__(self, engine_socket, namespace):
        """

        :param engine_socket: EngineSocket
        :param namespace: string
        :return:
        """
        super(Socket, self).__init__()

        self.ready_state = None

        self.engine_socket = engine_socket
        self.namespace = namespace
        self.ids = 0
        self.acks = {}

        self.recv_buffer = []
        self.send_buffer = []

        self.connected = False
        self.disconnected = True

    def _set_engine_socket(self, engine_socket):
        """
        Hook up with engine socket
        :param engine_socket: EngineSocket
        :return:
        """

        assert isinstance(engine_socket, EngineSocket)
        engine_socket.on('open', self.on_open)
        engine_socket.on('packet', self.on_packet)
        engine_socket.on('close', self.on_close)

    def _clean_engine_socket(self, engine_socket):
        assert isinstance(engine_socket, EngineSocket)

        engine_socket.remove_listener('open', self.on_open)
        engine_socket.remove_listener('packet', self.on_packet)
        engine_socket.remove_listener('close', self.on_close)

    def on_packet(self, packet):
        if packet['nsp'] != self.namespace:
            logger.warn('Namespace not match')
            return

        packet_type = packet['type']

        if packet_type == Parser.CONNECT:
            self.on_connect()
        elif packet_type == Parser.EVENT:
            self.on_event(packet)
        elif packet_type == Parser.BINARY_EVENT:
            self.on_event(packet)
        elif packet_type == Parser.ACK:
            self.on_ack(packet)
        elif packet_type == Parser.BINARY_ACK:
            self.on_bin_ack(packet)
        elif packet_type == Parser.DISCONNECT:
            self.on_disconnect()
        elif packet_type == Parser.ERROR:
            self.emit('error', packet['data'])
        else:
            logger.warn('Received unknown packet type [%d]' % packet_type)

    def on_open(self):
        logger.debug('transport is open - connecting')

        if '/' != self.namespace:
            self.packet({'type': Parser.CONNECT})

    def open(self):
        if self.connected:
            return

        self._set_engine_socket(self.engine_socket)
        self.engine_socket.open()

        if 'open' == self.ready_state:
            self.on_open()

    connect = open

    def send(self, packets):
        """
        Send the packets out
        :param packets: (list, tuple)
        :return:
        """
        self.emit('message', packets)

    def emit(self, event, *args, **kwargs):
        """
        :param event: string
        :param args: packets
        :param kwargs: 'callback' to specify a callback
        :return:
        """
        if event in internal_events:
            super(Socket, self).emit(event, *args, **kwargs)
            return

        parser_type = Parser.EVENT

        if has_bin(args):
            parser_type = Parser.BINARY_EVENT

        packet = {
            'type': parser_type,
            'data': args,
        }

        if 'callback' in kwargs:
            cb = kwargs['callback']
            self.acks[self.ids] = cb
            packet['id'] = self.ids
            self.ids += 1

        if self.connected:
            self.packet(packet)
        else:
            self.send_buffer.append(packet)

    def packet(self, packet):
        packet['nsp'] = self.namespace
        self.engine_socket.packet(packet)

    def on_event(self, packet):
        data = packet['data']
        logger.debug('emitting event: %s', data)

        if type(data) not in (list, tuple):
            data = [data]

        callback = None
        if 'id' in packet:
            logger.debug('attaching ack callback to event')
            callback = self.acks[packet['id']]

        if self.connected:
            self.emit(*data, callback=callback)
        else:
            self.recv_buffer.append((data, callback))

    def ack(self, _id):
        """
        Produce an ack callback
        :param _id:
        :return:
        """
        context = {
            'sent': False
        }

        def callback(packets):
            """
            callback which sends the ack packet to anti party
            :param packets: list | tuple
            :return:
            """
            if context['sent']:
                return

            context['sent'] = True
            packet_type = Parser.BINARY_ACK if has_bin(packets) else Parser.ACK

            self.packet({
                'type': packet_type,
                'id': _id,
                'data': packets
            })

        return callback

    def on_ack(self, packet):
        logger.debug('calling ack %s with %s', packet['id'], packet['data'])

        cb = self.acks.pop(packet['id'])
        cb(packet['data'])

    def on_connect(self):
        self.connected = True
        self.disconnected = False
        self.emit('connect')
        self.emit_buffered()

    def emit_buffered(self):
        logger.debug('emit buffered packets')

        for data, callback in self.recv_buffer:
            self.emit(*data, callback=callback)
        self.recv_buffer = []

        for packet in self.send_buffer:
            self.packet(packet)
        self.send_buffer = []

    def on_disconnect(self):
        logger.debug('server disconnect namespace [%s]', self.namespace)

        self.destroy()
        self.on_close('io server disconnect')

    def destroy(self):
        self._clean_engine_socket(self.engine_socket)
        self.engine_socket.destroy()
        self.engine_socket = None

    def on_close(self, reason=''):
        logger.debug('close (%s)', reason)

        self.connected = False
        self.disconnected = True
        self.emit('disconnect', reason)

    def close(self):
        if self.connected:
            logger.debug('performing disconnect (%s)', self.namespace)
            self.packet({
                'type': Parser.DISCONNECT
            })

        self.destroy()

        if self.connected:
            self.on_close('client disconnect')

    disconnect = close
